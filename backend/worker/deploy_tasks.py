from worker.celery_app import celery_app
from services.deploy_service import DeployEngine
from core.database import SessionLocal
from core.bt_api_client import BaotaAPI
from core.ssh_client import execute_remote_cmd
from core.obs_client import OBSClient
from core.runtime_paths import LANDING_PAGES_DIR
import models.server  # noqa: F401 - 确保 SQLAlchemy 能解析 sites.server_id 外键
from models.site import Site
from models.server import Server
from models.asset import TDKConfig
from models.asset import TemplatePackage
from models.asset import LandingPagePackage
from models.asset import PluginPackage, PluginVersion, SitePluginDeployment
from models.site_log import SiteDeployLog
from services.audit_service import update_task_log
from services.audit_service import log_operation
from services.tdk_switch_service import apply_tdk_to_remote_site
from services.plugin_deploy_service import deploy_redirect_plugin, upsert_site_plugin_deployment
import asyncio
import random
import string
import json
import os
import uuid
import redis
from datetime import datetime, timezone
import base64
import shlex
import re
import tempfile
import shutil
import zipfile


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
LOG_CHANNEL_PREFIX = "site_logs"
_redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
_obs_client = OBSClient()


def _safe_json_dumps(data):
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return "{}"


async def _panel_site_exists(server: Server, domain: str) -> bool:
    py_script = (
        "import os,sys,json\n"
        "os.chdir('/www/server/panel/')\n"
        "sys.path.insert(0,'/www/server/panel/')\n"
        "sys.path.insert(0,'class/')\n"
        "import public\n"
        f"domain = {json.dumps(domain)}\n"
        "cnt = public.M('sites').where('name=?',(domain,)).count()\n"
        "print(json.dumps({'status': True, 'exists': bool(cnt), 'count': int(cnt or 0)}, ensure_ascii=False))\n"
    )
    py_b64 = base64.b64encode(py_script.encode("utf-8")).decode("ascii")
    remote_py = f"/tmp/autocms_site_exists_{domain.replace('.', '_')}.py"
    cmd = (
        "bash -lc 'set -euo pipefail; "
        "pybin=/www/server/panel/pyenv/bin/python3.7; "
        "[ -x \"$pybin\" ] || pybin=/www/server/panel/pyenv/bin/python3; "
        "[ -x \"$pybin\" ] || pybin=python3; "
        f"printf %s {py_b64} | base64 -d > {shlex.quote(remote_py)}; "
        f"\"$pybin\" {shlex.quote(remote_py)}; "
        f"rm -f {shlex.quote(remote_py)}'"
    )
    out = await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), cmd, timeout_sec=60)
    parsed = None
    for line in reversed((out or "").splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
            break
        except Exception:
            continue
    if not isinstance(parsed, dict):
        raise Exception(f"检查站点是否存在失败: {(out or '').strip()[:240]}")
    return bool(parsed.get("exists"))


async def _panel_delete_site_by_script(server: Server, domain: str):
    py_script = (
        "import os,sys,json\n"
        "os.chdir('/www/server/panel/')\n"
        "sys.path.insert(0,'/www/server/panel/')\n"
        "sys.path.insert(0,'class/')\n"
        "import public\n"
        "import panelSite\n"
        f"domain = {json.dumps(domain)}\n"
        "site_id = public.M('sites').where('name=?',(domain,)).getField('id')\n"
        "if not site_id:\n"
        "    print(json.dumps({'status': True, 'msg': '站点不存在，按幂等继续'}, ensure_ascii=False)); raise SystemExit(0)\n"
        "args = public.dict_obj()\n"
        "args.id = str(site_id)\n"
        "args.webname = domain\n"
        "args.path = '/www/wwwroot/' + domain\n"
        "args.ftp = '1'\n"
        "args.database = '1'\n"
        "res = panelSite.panelSite().DeleteSite(args)\n"
        "print(json.dumps(res, ensure_ascii=False))\n"
    )
    py_b64 = base64.b64encode(py_script.encode("utf-8")).decode("ascii")
    remote_py = f"/tmp/autocms_site_delete_{domain.replace('.', '_')}.py"
    cmd = (
        "bash -lc 'set -euo pipefail; "
        "pybin=/www/server/panel/pyenv/bin/python3.7; "
        "[ -x \"$pybin\" ] || pybin=/www/server/panel/pyenv/bin/python3; "
        "[ -x \"$pybin\" ] || pybin=python3; "
        f"printf %s {py_b64} | base64 -d > {shlex.quote(remote_py)}; "
        f"\"$pybin\" {shlex.quote(remote_py)}; "
        f"rm -f {shlex.quote(remote_py)}'"
    )
    out = await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), cmd, timeout_sec=120)
    parsed = None
    for line in reversed((out or "").splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
            break
        except Exception:
            continue
    if not isinstance(parsed, dict):
        raise Exception(f"面板脚本删站返回异常: {(out or '').strip()[:300]}")
    status = str(parsed.get("status")).lower()
    if status not in {"1", "true", "ok", "success"}:
        raise Exception(str(parsed.get("msg") or "面板脚本删除站点失败"))
    return parsed


async def _apply_https_by_panel_script(server: Server, domain: str):
    py_script = (
        "import os,sys,json\n"
        "os.chdir('/www/server/panel/')\n"
        "sys.path.insert(0,'/www/server/panel/')\n"
        "sys.path.insert(0,'class/')\n"
        "import public\n"
        "from acme_v2 import acme_v2\n"
        "import panelSite\n"
        f"domain = {json.dumps(domain)}\n"
        "site_id = public.M('sites').where('name=?',(domain,)).getField('id')\n"
        "if not site_id:\n"
        "    print(json.dumps({'status': False, 'msg': '宝塔站点不存在'}, ensure_ascii=False)); raise SystemExit(0)\n"
        "args = public.dict_obj()\n"
        "args.id = int(site_id)\n"
        "args.domains = json.dumps([domain])\n"
        "args.auth_type = 'http'\n"
        "args.auth_to = str(args.id)\n"
        "acme = acme_v2()\n"
        "res = acme.apply_cert_api(args)\n"
        "if isinstance(res, dict) and res.get('status'):\n"
        "    if res.get('private_key') and res.get('cert'):\n"
        "        g = public.dict_obj()\n"
        "        g.siteName = domain\n"
        "        g.first_domain = domain\n"
        "        g.key = res.get('private_key')\n"
        "        g.csr = (res.get('cert') or '') + (res.get('root') or '')\n"
        "        set_res = panelSite.panelSite().SetSSL(g)\n"
        "        if isinstance(set_res, dict) and not set_res.get('status'):\n"
        "            print(json.dumps(set_res, ensure_ascii=False)); raise SystemExit(0)\n"
        "print(json.dumps(res, ensure_ascii=False))\n"
    )
    py_b64 = base64.b64encode(py_script.encode("utf-8")).decode("ascii")
    remote_py = f"/tmp/autocms_https_{domain.replace('.', '_')}.py"
    cmd = (
        "bash -lc 'set -euo pipefail; "
        "pybin=/www/server/panel/pyenv/bin/python3.7; "
        "[ -x \"$pybin\" ] || pybin=/www/server/panel/pyenv/bin/python3; "
        "[ -x \"$pybin\" ] || pybin=python3; "
        f"printf %s {py_b64} | base64 -d > {shlex.quote(remote_py)}; "
        f"\"$pybin\" {shlex.quote(remote_py)}; "
        f"rm -f {shlex.quote(remote_py)}'"
    )
    out = await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), cmd, timeout_sec=180)
    parsed = None
    for line in reversed((out or "").splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
            break
        except Exception:
            continue
    if not isinstance(parsed, dict):
        raise Exception(f"面板脚本返回异常: {(out or '').strip()[:300]}")
    if not parsed.get("status"):
        raise Exception(str(parsed.get("msg") or "面板脚本申请证书失败"))
    return parsed


async def _read_https_expire_at_from_remote(server: Server, domain: str):
    cert_a = shlex.quote(f"/www/server/panel/vhost/cert/{domain}/fullchain.pem")
    cert_b = shlex.quote(f"/etc/letsencrypt/live/{domain}/fullchain.pem")
    cmd = (
        "bash -lc 'set -euo pipefail; "
        f"for p in {cert_a} {cert_b}; do "
        "if [ -s \"$p\" ]; then openssl x509 -noout -enddate -in \"$p\" && exit 0; fi; "
        "done; exit 0'"
    )
    out = await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), cmd, timeout_sec=30)
    text = (out or "").strip()
    m = re.search(r"notAfter=(.+)", text)
    if not m:
        return None
    raw = m.group(1).strip()
    # Common format: "Jun 29 13:08:58 2026 GMT"
    try:
        dt = datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    # Fallback without timezone token
    try:
        dt = datetime.strptime(raw.replace(" GMT", ""), "%b %d %H:%M:%S %Y")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def _verify_https_enabled_remote(server: Server, domain: str) -> bool:
    conf = shlex.quote(f"/www/server/panel/vhost/nginx/{domain}.conf")
    cmd = (
        "bash -lc 'set -euo pipefail; "
        f"conf={conf}; "
        "[ -f \"$conf\" ] || exit 0; "
        "awk \"/ssl_certificate[[:space:]]+/ {print \\$2}\" \"$conf\" | tr -d \";\" | sed -n \"1p\"'"
    )
    cert_path = (await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), cmd, timeout_sec=20)).strip()
    if not cert_path:
        return False
    check_cmd = f"bash -lc 'test -s {shlex.quote(cert_path)} && echo OK || true'"
    out = await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), check_cmd, timeout_sec=20)
    return "OK" in (out or "")


def _extract_zip_to_local_preview(zip_path: str, target_dir: str):
    with tempfile.TemporaryDirectory(prefix="landing_unpack_") as tmp_dir:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)

        best_index = None
        best_depth = None
        for root, dirs, files in os.walk(tmp_dir):
            dirs[:] = [d for d in dirs if d != "__MACOSX"]
            if "index.html" not in files:
                continue
            rel = os.path.relpath(root, tmp_dir)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if best_index is None or depth < best_depth:
                best_index = os.path.join(root, "index.html")
                best_depth = depth

        if not best_index:
            raise Exception("落地页压缩包缺少 index.html")
        source_dir = os.path.dirname(best_index)

        os.makedirs(target_dir, exist_ok=True)
        for item in os.listdir(target_dir):
            path = os.path.join(target_dir, item)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except Exception:
                    pass
        for item in os.listdir(source_dir):
            src = os.path.join(source_dir, item)
            dst = os.path.join(target_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)


async def _apply_landing_to_remote_site(server: Server, domain: str, obs_url: str, cache_id: str):
    esc_site_dir = shlex.quote(f"/www/wwwroot/{domain}")
    cache_file = f"/www/cache/autocms/landing_{cache_id}.zip"
    esc_cache_file = shlex.quote(cache_file)
    obs_url_b64 = base64.b64encode((obs_url or "").encode("utf-8")).decode("ascii")

    # 先利用服务器缓存下载，避免重复消耗 OBS 流量。
    fetch_cmd = (
        "bash -lc 'set -euo pipefail; "
        f"url=$(printf %s {obs_url_b64} | base64 -d); "
        "mkdir -p /www/cache/autocms; "
        f"if [ ! -s {esc_cache_file} ]; then wget -nv --timeout=25 --tries=2 -O {esc_cache_file} \"$url\"; fi'"
    )
    await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), fetch_cmd, timeout_sec=180)

    py_script = (
        "import os, json, shutil, tempfile, zipfile\n"
        f"site_dir = {json.dumps(f'/www/wwwroot/{domain}')}\n"
        f"cache_file = {json.dumps(cache_file)}\n"
        "target = os.path.join(site_dir, 'ldy')\n"
        "if not os.path.isfile(cache_file) or os.path.getsize(cache_file) <= 0:\n"
        "    print(json.dumps({'status': False, 'msg': '缓存zip不存在或为空'}, ensure_ascii=False)); raise SystemExit(0)\n"
        "best_index = None\n"
        "best_depth = None\n"
        "tmp_dir = tempfile.mkdtemp(prefix='autocms_landing_')\n"
        "try:\n"
        "    with zipfile.ZipFile(cache_file, 'r') as zf:\n"
        "        zf.extractall(tmp_dir)\n"
        "    for root, dirs, files in os.walk(tmp_dir):\n"
        "        dirs[:] = [d for d in dirs if d != '__MACOSX']\n"
        "        if 'index.html' not in files:\n"
        "            continue\n"
        "        rel = os.path.relpath(root, tmp_dir)\n"
        "        depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
        "        if best_index is None or depth < best_depth:\n"
        "            best_index = os.path.join(root, 'index.html')\n"
        "            best_depth = depth\n"
        "    if not best_index:\n"
        "        print(json.dumps({'status': False, 'msg': '落地页压缩包缺少index.html'}, ensure_ascii=False)); raise SystemExit(0)\n"
        "    src_dir = os.path.dirname(best_index)\n"
        "    os.makedirs(target, exist_ok=True)\n"
        "    for item in os.listdir(target):\n"
        "        p = os.path.join(target, item)\n"
        "        if os.path.isdir(p):\n"
        "            shutil.rmtree(p, ignore_errors=True)\n"
        "        else:\n"
        "            try:\n"
        "                os.remove(p)\n"
        "            except Exception:\n"
        "                pass\n"
        "    for item in os.listdir(src_dir):\n"
        "        src = os.path.join(src_dir, item)\n"
        "        dst = os.path.join(target, item)\n"
        "        if os.path.isdir(src):\n"
        "            if os.path.exists(dst):\n"
        "                shutil.rmtree(dst, ignore_errors=True)\n"
        "            shutil.copytree(src, dst)\n"
        "        else:\n"
        "            shutil.copy2(src, dst)\n"
        "    print(json.dumps({'status': True, 'msg': 'ok'}, ensure_ascii=False))\n"
        "finally:\n"
        "    shutil.rmtree(tmp_dir, ignore_errors=True)\n"
    )
    py_b64 = base64.b64encode(py_script.encode("utf-8")).decode("ascii")
    remote_py = f"/tmp/autocms_apply_landing_{domain.replace('.', '_')}.py"
    run_cmd = (
        "bash -lc 'set -euo pipefail; "
        "pybin=/www/server/panel/pyenv/bin/python3.7; "
        "[ -x \"$pybin\" ] || pybin=/www/server/panel/pyenv/bin/python3; "
        "[ -x \"$pybin\" ] || pybin=python3; "
        f"printf %s {py_b64} | base64 -d > {shlex.quote(remote_py)}; "
        f"\"$pybin\" {shlex.quote(remote_py)}; "
        f"rm -f {shlex.quote(remote_py)}'"
    )
    out = await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), run_cmd, timeout_sec=300)
    parsed = None
    for line in reversed((out or "").splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
            break
        except Exception:
            continue
    if not isinstance(parsed, dict) or not parsed.get("status"):
        raise Exception(str((parsed or {}).get("msg") or f"落地页下发失败: {(out or '').strip()[:300]}"))


def _write_log(db, site_id, stage, message, level="info"):
    log = SiteDeployLog(site_id=site_id, stage=stage, message=message, level=level)
    db.add(log)
    db.commit()
    db.refresh(log)

    payload = {
        "id": log.id,
        "site_id": log.site_id,
        "level": log.level,
        "stage": log.stage,
        "message": log.message,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }
    try:
        _redis_client.publish(f"{LOG_CHANNEL_PREFIX}:{site_id}", json.dumps(payload, ensure_ascii=False))
    except Exception:
        # Redis 推送失败不影响主流程，前端可回退到 REST 查询
        pass


@celery_app.task(bind=True)
def process_single_site(self, site_id, server_ip, domain, bind_ip, core_key, template_key, tdk_config, admin_path, admin_username, admin_password, host_headers, retry_limit, bt_url, bt_key, task_log_id=None, ssh_port=22):
    retry_limit = max(0, min(int(retry_limit or 0), 5))

    def _is_retryable_error(message: str) -> bool:
        text = (message or "").lower()
        retryable_keywords = [
            "timeout",
            "timed out",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "network",
            "name or service not known",
            "ssh 执行失败",
            "wget",
            "redis",
            "obs",
        ]
        non_retryable_keywords = [
            "admin_path 非法",
            "host_headers 非法",
            "找不到服务器",
            "permission denied",
            "syntax error",
            "no such file",
            "域名已存在",
        ]
        if any(k in text for k in non_retryable_keywords):
            return False
        return any(k in text for k in retryable_keywords)

    # 1. 实例化核心上站引擎
    engine = DeployEngine(server_ip=server_ip, bt_url=bt_url, bt_key=bt_key, ssh_port=int(ssh_port or 22))

    # 2. 动态生成宝塔数据库名和密码
    db_name = domain.replace('.', '_')[:10] + "".join(random.choices(string.ascii_lowercase, k=4))
    db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    db = SessionLocal()
    try:
        if task_log_id:
            update_task_log(db, int(task_log_id), status="running", message=f"执行中: {domain}")
        attempt_no = int(self.request.retries or 0) + 1
        total_attempts = retry_limit + 1
        host_txt = ",".join(host_headers or [])
        _write_log(db, site_id, "start", f"开始部署: {domain} -> {bind_ip}，主机头: {host_txt}，SSH端口: {engine.ssh_port}，第 {attempt_no}/{total_attempts} 次尝试")
        _write_log(db, site_id, "bt", "正在调用宝塔 API 创建站点与数据库")

        # 3. 执行真正的部署流水线 (注意这里的参数全部带上了名字)
        result = asyncio.run(engine.execute_eyoucms_deployment(
            domain=domain, 
            db_name=db_name, 
            db_user=db_name, 
            db_pass=db_pass,
            admin_path=admin_path, 
            admin_username=admin_username,
            admin_password=admin_password,
            tdk_config=tdk_config, 
            core_obs_key=core_key,
            tpl_obs_key=template_key,
            host_headers=host_headers,
            on_progress=lambda stage, message: _write_log(db, site_id, stage, message),
        ))
        
        # 4. 部署成功，更新数据库状态
        site = db.query(Site).filter(Site.id == site_id).first()
        if site:
            site.status = "success"
            db.commit()
            _write_log(db, site_id, "done", "部署完成", "success")
        if task_log_id:
            update_task_log(
                db,
                int(task_log_id),
                status="success",
                message=f"执行完成: {domain}",
                detail={"site_id": site_id, "domain": domain, "status": "success"},
                task_ref=self.request.id,
            )
            
        return result
    except Exception as e:
        print(f"❌ 部署失败 [{domain}]: {str(e)}")
        msg = str(e)
        retryable = _is_retryable_error(msg)
        current_retry = int(self.request.retries or 0)
        if retryable and current_retry < retry_limit:
            next_attempt = current_retry + 2
            total_attempts = retry_limit + 1
            countdown = min(300, 20 * (2 ** current_retry))
            _write_log(
                db,
                site_id,
                "retry",
                f"检测到可重试异常，将在 {countdown}s 后执行第 {next_attempt}/{total_attempts} 次重试：{msg}",
                "warning",
            )
            raise self.retry(exc=e, countdown=countdown, max_retries=retry_limit)

        # 5. 部署失败，更新数据库状态和错误信息
        site = db.query(Site).filter(Site.id == site_id).first()
        if site:
            site.status = "failed"
            if retryable and retry_limit > 0:
                site.error_msg = f"[可重试异常已耗尽] {msg}"
                _write_log(db, site_id, "error_class", "异常分级：可重试异常（重试次数耗尽）", "warning")
            else:
                site.error_msg = f"[不可重试异常] {msg}"
                _write_log(db, site_id, "error_class", "异常分级：不可重试异常（直接失败）", "warning")
            db.commit()
            _write_log(db, site_id, "error", msg, "error")
        if task_log_id:
            update_task_log(
                db,
                int(task_log_id),
                status="failed",
                message=f"执行失败: {domain}",
                detail={"site_id": site_id, "domain": domain, "status": "failed", "error": msg},
                task_ref=self.request.id,
            )
        raise e
    finally:
        db.close()


@celery_app.task(bind=True)
def process_batch_switch_tdk(self, task_log_id, site_ids, tdk_id):
    db = SessionLocal()
    try:
        update_task_log(db, int(task_log_id), status="running", message="批量TDK切换执行中", task_ref=self.request.id)
        tdk = db.query(TDKConfig).filter(TDKConfig.id == int(tdk_id)).first()
        if not tdk:
            raise Exception("TDK方案不存在")
        sites = db.query(Site).filter(Site.id.in_(site_ids or [])).all()
        server_map = {s.id: s for s in db.query(Server).all()}
        updated = 0
        failed = []
        for site in sites:
            server = server_map.get(site.server_id)
            if not server:
                failed.append({"site_id": site.id, "domain": site.domain, "reason": "找不到对应服务器"})
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="tdk_switch", message="切换失败：找不到对应服务器"))
                db.commit()
                continue
            try:
                asyncio.run(apply_tdk_to_remote_site(site, server, tdk))
                site.tdk_name = tdk.name
                site.tdk_title = tdk.title
                site.tdk_keywords = tdk.keywords
                site.tdk_description = tdk.description
                db.add(SiteDeployLog(site_id=site.id, level="info", stage="tdk_switch", message=f"已切换TDK方案（站点已同步）: {tdk.name}"))
                db.commit()
                updated += 1
            except Exception as exc:
                failed.append({"site_id": site.id, "domain": site.domain, "reason": str(exc)})
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="tdk_switch", message=f"切换失败: {exc}"))
                db.commit()

        status = "success" if not failed else ("success" if updated > 0 else "failed")
        update_task_log(
            db,
            int(task_log_id),
            status=status,
            message=f"批量TDK切换完成：成功 {updated}，失败 {len(failed)}",
            detail={"updated": updated, "failed": failed, "site_ids": site_ids, "tdk_id": tdk_id},
            task_ref=self.request.id,
        )
        return {"updated": updated, "failed": failed}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"批量TDK切换失败: {exc}",
            detail={"site_ids": site_ids, "tdk_id": tdk_id, "error": str(exc)},
            task_ref=self.request.id,
        )
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def process_tdk_batch_import(self, task_log_id, tdks):
    db = SessionLocal()
    try:
        update_task_log(db, int(task_log_id), status="running", message="批量导入TDK执行中", task_ref=self.request.id)
        payload = tdks or []
        created = 0
        for row in payload:
            item = TDKConfig(
                name=str((row or {}).get("name") or "").strip(),
                title=str((row or {}).get("title") or "").strip(),
                keywords=str((row or {}).get("keywords") or "").strip(),
                description=str((row or {}).get("description") or "").strip(),
            )
            db.add(item)
            created += 1
        db.commit()
        log_operation(
            db,
            action="tdk.batch_create",
            message=f"批量导入TDK完成: {created} 条",
            detail={"count": created},
        )
        update_task_log(
            db,
            int(task_log_id),
            status="success",
            message=f"批量导入TDK完成: {created} 条",
            detail={"count": created},
            task_ref=self.request.id,
        )
        return {"count": created}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"批量导入TDK失败: {exc}",
            detail={"error": str(exc), "count": len(tdks or [])},
            task_ref=self.request.id,
        )
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def process_template_upload(self, task_log_id, file_path, pkg_type, name, original_filename):
    db = SessionLocal()
    try:
        update_task_log(db, int(task_log_id), status="running", message=f"资源包上传执行中: {name}", task_ref=self.request.id)
        if not os.path.exists(file_path):
            raise Exception("临时文件不存在，无法上传")
        folder = "eyoucms/core" if pkg_type == "core" else "eyoucms/muban"
        safe_filename = f"{uuid.uuid4().hex[:8]}_{original_filename}"
        obs_key = f"{folder}/{safe_filename}"
        with open(file_path, "rb") as fp:
            file_bytes = fp.read()
        _obs_client.upload_file_bytes(obs_key, file_bytes)
        new_template = TemplatePackage(name=name, pkg_type=pkg_type, obs_path=obs_key)
        db.add(new_template)
        db.commit()
        db.refresh(new_template)
        log_operation(
            db,
            action="template.upload",
            message=f"上传资源包完成: {name} ({pkg_type})",
            detail={"template_id": new_template.id, "obs_key": obs_key, "pkg_type": pkg_type},
        )
        update_task_log(
            db,
            int(task_log_id),
            status="success",
            message=f"资源包上传完成: {name}",
            detail={"template_id": new_template.id, "obs_key": obs_key, "pkg_type": pkg_type},
            task_ref=self.request.id,
        )
        return {"template_id": new_template.id, "obs_key": obs_key}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"资源包上传失败: {exc}",
            detail={"name": name, "pkg_type": pkg_type, "error": str(exc)},
            task_ref=self.request.id,
        )
        raise
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        db.close()


@celery_app.task(bind=True)
def process_landing_upload(self, task_log_id, file_path, name, remark="", username=None, landing_page_id=None, original_filename=""):
    db = SessionLocal()
    try:
        update_task_log(db, int(task_log_id), status="running", message=f"落地页上传执行中: {name}", task_ref=self.request.id)
        if not os.path.exists(file_path):
            raise Exception("临时文件不存在，无法上传")

        with open(file_path, "rb") as fp:
            file_bytes = fp.read()
        if not file_bytes:
            raise Exception("上传文件为空")

        safe_filename = f"{uuid.uuid4().hex[:8]}_{(original_filename or 'landing.zip')}"
        obs_key = f"eyoucms/landing/{safe_filename}"
        _obs_client.upload_file_bytes(obs_key, file_bytes)

        if landing_page_id:
            item = db.query(LandingPagePackage).filter(LandingPagePackage.id == int(landing_page_id)).first()
            if not item:
                raise Exception("落地页记录不存在，无法覆盖")
            item.name = str(name or item.name)
            item.remark = str(remark or "")
            item.username = username or item.username
            item.obs_path = obs_key
        else:
            item = LandingPagePackage(
                name=str(name or "").strip() or f"落地页-{uuid.uuid4().hex[:6]}",
                obs_path=obs_key,
                remark=str(remark or ""),
                username=(username or None),
            )
            db.add(item)
        db.commit()
        db.refresh(item)

        local_dir = str(LANDING_PAGES_DIR / str(item.id))
        _extract_zip_to_local_preview(file_path, local_dir)
        preview_url = f"/landing_pages/{item.id}/index.html"

        action = "landing.upload.cover" if landing_page_id else "landing.upload"
        log_operation(
            db,
            action=action,
            message=f"{'覆盖上传' if landing_page_id else '上传'}落地页完成: {item.name}",
            detail={"landing_page_id": item.id, "obs_key": obs_key, "preview_url": preview_url, "username": username},
        )
        update_task_log(
            db,
            int(task_log_id),
            status="success",
            message=f"落地页上传完成: {item.name}",
            detail={"landing_page_id": item.id, "obs_key": obs_key, "preview_url": preview_url},
            task_ref=self.request.id,
        )
        return {"landing_page_id": item.id, "obs_key": obs_key, "preview_url": preview_url}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"落地页上传失败: {exc}",
            detail={"name": name, "error": str(exc), "landing_page_id": landing_page_id},
            task_ref=self.request.id,
        )
        raise
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        db.close()


@celery_app.task(bind=True)
def process_batch_switch_landing(self, task_log_id, site_ids, landing_page_id):
    db = SessionLocal()
    try:
        update_task_log(db, int(task_log_id), status="running", message="批量配置落地页执行中", task_ref=self.request.id)
        landing = db.query(LandingPagePackage).filter(LandingPagePackage.id == int(landing_page_id)).first()
        if not landing:
            raise Exception("落地页不存在")
        obs_url = _obs_client.get_presigned_url(landing.obs_path)
        if not obs_url:
            raise Exception("落地页OBS临时链接生成失败")
        cache_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(landing.obs_path)).hex[:16]

        sites = db.query(Site).filter(Site.id.in_(site_ids or [])).all()
        server_map = {s.id: s for s in db.query(Server).all()}
        done = 0
        failed = []
        for site in sites:
            server = server_map.get(site.server_id)
            if not server:
                failed.append({"site_id": site.id, "domain": site.domain, "reason": "找不到对应服务器"})
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="landing", message="配置落地页失败：找不到对应服务器"))
                db.commit()
                continue
            try:
                asyncio.run(_apply_landing_to_remote_site(server, site.domain, obs_url, cache_id))
                site.landing_page_id = int(landing.id)
                site.landing_page_name = landing.name
                db.add(SiteDeployLog(site_id=site.id, level="success", stage="landing", message=f"落地页已配置: {landing.name}"))
                db.commit()
                done += 1
            except Exception as exc:
                failed.append({"site_id": site.id, "domain": site.domain, "reason": str(exc)})
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="landing", message=f"配置落地页失败: {exc}"))
                db.commit()

        status = "success" if not failed else ("success" if done > 0 else "failed")
        update_task_log(
            db,
            int(task_log_id),
            status=status,
            message=f"批量配置落地页完成：成功 {done}，失败 {len(failed)}",
            detail={"site_ids": site_ids, "landing_page_id": int(landing_page_id), "landing_page_name": landing.name, "done": done, "failed": failed},
            task_ref=self.request.id,
        )
        log_operation(
            db,
            action="site.batch_switch_landing.done",
            message=f"批量配置落地页完成：成功 {done}，失败 {len(failed)}",
            detail={"site_ids": site_ids, "landing_page_id": int(landing_page_id), "done": done, "failed": failed},
        )
        return {"done": done, "failed": failed}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"批量配置落地页失败: {exc}",
            detail={"site_ids": site_ids, "landing_page_id": landing_page_id, "error": str(exc)},
            task_ref=self.request.id,
        )
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def process_batch_enable_https(self, task_log_id, site_ids, force_renew=True):
    db = SessionLocal()
    try:
        update_task_log(db, int(task_log_id), status="running", message="批量HTTPS配置执行中", task_ref=self.request.id)
        sites = db.query(Site).filter(Site.id.in_(site_ids or [])).all()
        server_map = {s.id: s for s in db.query(Server).all()}
        done = 0
        failed = []
        for site in sites:
            server = server_map.get(site.server_id)
            if not server:
                failed.append({"site_id": site.id, "domain": site.domain, "reason": "找不到对应服务器"})
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="https", message="HTTPS配置失败：找不到对应服务器"))
                db.commit()
                continue
            try:
                bt = BaotaAPI(f"{server.bt_protocol}://{server.main_ip}:{server.bt_port}", server.bt_key)
                expire_at = None
                try:
                    asyncio.run(bt.apply_https_letsencrypt(site.domain, force_renew=bool(force_renew)))
                    expire_at = asyncio.run(bt.get_https_expire_at(site.domain))
                except Exception:
                    # 宝塔外部API在不同版本参数差异大，回退到面板内置脚本入口。
                    asyncio.run(_apply_https_by_panel_script(server, site.domain))
                    try:
                        expire_at = asyncio.run(bt.get_https_expire_at(site.domain))
                    except Exception:
                        expire_at = None
                if not expire_at:
                    try:
                        expire_at = asyncio.run(_read_https_expire_at_from_remote(server, site.domain))
                    except Exception:
                        expire_at = None
                enabled_remote = False
                try:
                    enabled_remote = asyncio.run(_verify_https_enabled_remote(server, site.domain))
                except Exception:
                    enabled_remote = False
                if not enabled_remote:
                    raise Exception("HTTPS未在远端配置文件生效")
                site.https_enabled = True
                site.https_auto_renew = True
                site.https_expire_at = expire_at
                site.https_updated_at = datetime.now(timezone.utc)
                db.add(
                    SiteDeployLog(
                        site_id=site.id,
                        level="success",
                        stage="https",
                        message="HTTPS已配置（Let's Encrypt + 自动续期）",
                    )
                )
                db.commit()
                done += 1
            except Exception as exc:
                # 某些场景（如 Let's Encrypt 频率限制）虽然申请失败，但站点可能已存在可用HTTPS证书。
                enabled_remote = False
                try:
                    enabled_remote = asyncio.run(_verify_https_enabled_remote(server, site.domain))
                except Exception:
                    enabled_remote = False
                if enabled_remote:
                    expire_at = None
                    try:
                        expire_at = asyncio.run(_read_https_expire_at_from_remote(server, site.domain))
                    except Exception:
                        expire_at = None
                    site.https_enabled = True
                    site.https_auto_renew = True
                    site.https_expire_at = expire_at
                    site.https_updated_at = datetime.now(timezone.utc)
                    db.add(
                        SiteDeployLog(
                            site_id=site.id,
                            level="warning",
                            stage="https",
                            message=f"HTTPS申请返回异常，但检测到已生效: {exc}",
                        )
                    )
                    db.commit()
                    done += 1
                    continue

                failed.append({"site_id": site.id, "domain": site.domain, "reason": str(exc)})
                site.https_enabled = False
                site.https_updated_at = datetime.now(timezone.utc)
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="https", message=f"HTTPS配置失败: {exc}"))
                db.commit()

        status = "success" if not failed else ("success" if done > 0 else "failed")
        update_task_log(
            db,
            int(task_log_id),
            status=status,
            message=f"批量HTTPS配置完成：成功 {done}，失败 {len(failed)}",
            detail={"done": done, "failed": failed, "site_ids": site_ids},
            task_ref=self.request.id,
        )
        log_operation(
            db,
            action="site.batch_enable_https.done",
            message=f"批量配置HTTPS完成：成功 {done}，失败 {len(failed)}",
            detail={"site_ids": site_ids, "done": done, "failed": failed},
        )
        return {"done": done, "failed": failed}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"批量HTTPS配置失败: {exc}",
            detail={"site_ids": site_ids, "error": str(exc)},
            task_ref=self.request.id,
        )
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def process_batch_delete_sites(self, task_log_id, site_ids, purge_bt=True):
    db = SessionLocal()
    try:
        ids = sorted({int(x) for x in (site_ids or [])})
        update_task_log(
            db,
            int(task_log_id),
            status="running",
            message=f"批量删除站点执行中（{len(ids)}）",
            detail={"site_ids": ids, "purge_bt": bool(purge_bt)},
            task_ref=self.request.id,
        )
        sites = db.query(Site).filter(Site.id.in_(ids)).all()
        server_map = {s.id: s for s in db.query(Server).all()}

        deleted = 0
        bt_deleted = 0
        failed = []
        for site in sites:
            try:
                if purge_bt:
                    server = server_map.get(site.server_id)
                    if not server:
                        raise Exception("找不到对应服务器")
                    bt = BaotaAPI(f"{server.bt_protocol}://{server.main_ip}:{server.bt_port}", server.bt_key)
                    try:
                        asyncio.run(bt.delete_site(site.domain))
                    except Exception:
                        # 兼容不同版本面板，HTTP API失败时走面板内置脚本删除。
                        asyncio.run(_panel_delete_site_by_script(server, site.domain))
                    still_exists = asyncio.run(_panel_site_exists(server, site.domain))
                    if still_exists:
                        # 双保险再尝试一次脚本删除，然后强校验。
                        asyncio.run(_panel_delete_site_by_script(server, site.domain))
                        still_exists = asyncio.run(_panel_site_exists(server, site.domain))
                    if still_exists:
                        raise Exception("宝塔站点仍存在，未通过远端校验")
                    bt_deleted += 1

                db.query(SiteDeployLog).filter(SiteDeployLog.site_id == site.id).delete(synchronize_session=False)
                db.delete(site)
                db.commit()
                deleted += 1
            except Exception as exc:
                db.rollback()
                failed.append({"site_id": site.id, "domain": site.domain, "reason": str(exc)})
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="delete", message=f"删除失败: {exc}"))
                db.commit()

        status = "success" if not failed else ("success" if deleted > 0 else "failed")
        update_task_log(
            db,
            int(task_log_id),
            status=status,
            message=f"批量删除完成：本地成功 {deleted}，失败 {len(failed)}，宝塔成功 {bt_deleted}",
            detail={"site_ids": ids, "deleted": deleted, "failed": failed, "bt_deleted": bt_deleted, "purge_bt": bool(purge_bt)},
            task_ref=self.request.id,
        )
        log_operation(
            db,
            action="site.batch_delete.done",
            message=f"批量删除站点完成：本地成功 {deleted}，失败 {len(failed)}，宝塔成功 {bt_deleted}",
            detail={"site_ids": ids, "deleted": deleted, "failed": failed, "bt_deleted": bt_deleted, "purge_bt": bool(purge_bt)},
        )
        return {"deleted": deleted, "failed": failed, "bt_deleted": bt_deleted}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"批量删除站点失败: {exc}",
            detail={"site_ids": site_ids, "purge_bt": bool(purge_bt), "error": str(exc)},
            task_ref=self.request.id,
        )
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def process_plugin_redeploy_batch(self, task_log_id, plugin_id, target_mode="all_sites", site_id=None, server_id=None, version=None, site_ids=None):
    db = SessionLocal()
    try:
        update_task_log(
            db,
            int(task_log_id),
            status="running",
            message="插件重部署执行中",
            detail={
                "plugin_id": int(plugin_id),
                "target_mode": target_mode,
                "site_id": site_id,
                "site_ids": site_ids,
                "server_id": server_id,
                "version": version,
            },
            task_ref=self.request.id,
        )
        plugin = db.query(PluginPackage).filter(PluginPackage.id == int(plugin_id)).first()
        if not plugin:
            raise Exception("插件不存在")
        plugin_cfg = {}
        try:
            plugin_cfg = json.loads(plugin.config_json or "{}")
        except Exception:
            plugin_cfg = {}
        use_version = str(version or plugin.current_version or "").strip()
        if not use_version:
            raise Exception("插件版本为空")
        ver = (
            db.query(PluginVersion)
            .filter(PluginVersion.plugin_id == plugin.id, PluginVersion.version == use_version)
            .order_by(PluginVersion.id.desc())
            .first()
        )
        if not ver:
            raise Exception("插件版本不存在")
        try:
            cfg_snapshot = json.loads(ver.config_snapshot_json or "{}")
            if isinstance(cfg_snapshot, dict) and cfg_snapshot:
                plugin_cfg = cfg_snapshot
        except Exception:
            pass

        deployment_q = db.query(SitePluginDeployment).filter(SitePluginDeployment.plugin_id == plugin.id)
        if target_mode == "single_site":
            picked = sorted({int(x) for x in (site_ids or []) if int(x) > 0})
            if site_id and int(site_id) > 0:
                picked.append(int(site_id))
                picked = sorted(set(picked))
            if not picked:
                raise Exception("single_site 模式必须提供 site_id/site_ids")
            deployment_q = deployment_q.filter(SitePluginDeployment.site_id.in_(picked))
        elif target_mode == "single_server":
            if not server_id:
                raise Exception("single_server 模式必须提供 server_id")
            deployment_q = deployment_q.join(Site, Site.id == SitePluginDeployment.site_id).filter(Site.server_id == int(server_id))
        deployments = deployment_q.all()
        if not deployments:
            raise Exception("未找到可重部署站点")
        site_ids = sorted({int(d.site_id) for d in deployments})
        dep_map = {int(d.site_id): d for d in deployments}
        sites = db.query(Site).filter(Site.id.in_(site_ids)).all()
        server_map = {s.id: s for s in db.query(Server).all()}

        done = 0
        failed = []
        for site in sites:
            server = server_map.get(site.server_id)
            if not server:
                failed.append({"site_id": site.id, "domain": site.domain, "reason": "找不到对应服务器"})
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="plugin", message="插件重部署失败：找不到对应服务器"))
                db.commit()
                continue
            dep = dep_map.get(site.id)
            enabled = bool(int(getattr(dep, "enabled", 1) or 0))
            if not enabled:
                upsert_site_plugin_deployment(
                    db,
                    site_id=site.id,
                    plugin_id=plugin.id,
                    version=use_version,
                    enabled=False,
                    status="success",
                    error_msg=None,
                    task_log_id=int(task_log_id),
                )
                db.commit()
                done += 1
                continue
            try:
                asyncio.run(deploy_redirect_plugin(site, server, plugin_cfg, use_version))
                upsert_site_plugin_deployment(
                    db,
                    site_id=site.id,
                    plugin_id=plugin.id,
                    version=use_version,
                    enabled=True,
                    status="success",
                    error_msg=None,
                    task_log_id=int(task_log_id),
                )
                site.redirect_enabled = True
                site.redirect_ip_whitelist = str(plugin_cfg.get("ip_whitelist") or "")
                db.add(SiteDeployLog(site_id=site.id, level="success", stage="plugin", message=f"插件已重部署: {plugin.name} v{use_version}"))
                db.commit()
                done += 1
            except Exception as exc:
                db.rollback()
                failed.append({"site_id": site.id, "domain": site.domain, "reason": str(exc)})
                upsert_site_plugin_deployment(
                    db,
                    site_id=site.id,
                    plugin_id=plugin.id,
                    version=use_version,
                    enabled=True,
                    status="failed",
                    error_msg=str(exc),
                    task_log_id=int(task_log_id),
                )
                db.add(SiteDeployLog(site_id=site.id, level="error", stage="plugin", message=f"插件重部署失败: {exc}"))
                db.commit()

        status = "success" if not failed else ("success" if done > 0 else "failed")
        update_task_log(
            db,
            int(task_log_id),
            status=status,
            message=f"插件重部署完成：成功 {done}，失败 {len(failed)}",
            detail={
                "plugin_id": plugin.id,
                "plugin_name": plugin.name,
                "version": use_version,
                "target_mode": target_mode,
                "site_id": site_id,
                "site_ids": site_ids,
                "server_id": server_id,
                "done": done,
                "failed": failed,
            },
            task_ref=self.request.id,
        )
        log_operation(
            db,
            action="plugin.redeploy.done",
            message=f"插件重部署完成：成功 {done}，失败 {len(failed)}",
            detail={"plugin_id": plugin.id, "version": use_version, "done": done, "failed": failed, "target_mode": target_mode},
        )
        return {"done": done, "failed": failed}
    except Exception as exc:
        update_task_log(
            db,
            int(task_log_id),
            status="failed",
            message=f"插件重部署失败: {exc}",
            detail={"plugin_id": plugin_id, "target_mode": target_mode, "site_id": site_id, "site_ids": site_ids, "server_id": server_id, "version": version, "error": str(exc)},
            task_ref=self.request.id,
        )
        raise
    finally:
        db.close()