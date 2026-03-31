import asyncio
import json
import os
import shlex
import re
import base64
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from redis import asyncio as redis_async

from core.database import get_db, SessionLocal
from core.ssh_client import execute_remote_cmd
from worker.deploy_tasks import process_batch_switch_tdk, process_batch_enable_https, process_batch_delete_sites
from models.site import Site
from models.server import Server
from models.asset import TDKConfig
from models.site_log import SiteDeployLog
from services.audit_service import create_task_log, update_task_log, log_operation
from schemas.site import SitePageResponse, SiteBatchDeleteRequest, SiteBatchSwitchTdkRequest, SiteBatchHttpsRequest, SiteDeployLogResponse

router = APIRouter()
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
LOG_CHANNEL_PREFIX = "site_logs"


def _safe_sql_text(value: str) -> str:
    return str(value or "").replace("'", "''")


def _safe_ident(value: str) -> str:
    text = str(value or "").strip()
    if not re.match(r"^[A-Za-z0-9_]+$", text):
        raise Exception(f"非法标识符: {text}")
    return text


async def _run_remote_mysql(
    server_ip: str,
    ssh_port: int,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_pass: str,
    sql: str,
    seq: int,
) -> str:
    sql_path = f"/tmp/autocms_tdk_switch_{seq}.sql"
    esc_sql_path = shlex.quote(sql_path)
    sql_b64 = base64.b64encode(((sql or "").strip() + "\n").encode("utf-8")).decode("ascii")
    cmd = (
        "bash -lc 'set -euo pipefail; "
        f"printf %s {sql_b64} | base64 -d > {esc_sql_path}; "
        f"mysql --connect-timeout=20 -N -h{shlex.quote(db_host)} -P{int(db_port)} "
        f"-u{shlex.quote(db_user)} -p{shlex.quote(db_pass)} {shlex.quote(db_name)} < {esc_sql_path}; "
        f"rm -f {esc_sql_path}'"
    )
    return await execute_remote_cmd(server_ip, int(ssh_port or 22), cmd, timeout_sec=90)


async def _load_remote_db_config(server_ip: str, ssh_port: int, domain: str) -> dict:
    site_dir = f"/www/wwwroot/{domain}"
    esc_site_dir = shlex.quote(site_dir)
    php_script = (
        "<?php\n"
        f"$siteDir = {json.dumps(site_dir, ensure_ascii=False)};\n"
        "$files = [\n"
        "    $siteDir . '/data/conf/database.php',\n"
        "    $siteDir . '/config/database.php',\n"
        "    $siteDir . '/application/database.php',\n"
        "];\n"
        "$cfg = null;\n"
        "foreach ($files as $f) {\n"
        "    if (is_file($f)) {\n"
        "        $tmp = include $f;\n"
        "        if (is_array($tmp)) { $cfg = $tmp; break; }\n"
        "    }\n"
        "}\n"
        "if (!is_array($cfg)) { fwrite(STDERR, 'db config not found'); exit(2); }\n"
        "$result = [\n"
        "    'host' => strval($cfg['hostname'] ?? '127.0.0.1'),\n"
        "    'port' => intval($cfg['hostport'] ?? 3306),\n"
        "    'database' => strval($cfg['database'] ?? ''),\n"
        "    'username' => strval($cfg['username'] ?? ''),\n"
        "    'password' => strval($cfg['password'] ?? ''),\n"
        "    'prefix' => strval($cfg['prefix'] ?? 'ey_'),\n"
        "];\n"
        "echo json_encode($result, JSON_UNESCAPED_UNICODE);\n"
    )
    php_b64 = base64.b64encode(php_script.encode("utf-8")).decode("ascii")
    remote_php = f"/tmp/autocms_dbcfg_{domain.replace('.', '_')}.php"
    esc_remote_php = shlex.quote(remote_php)
    cmd = (
        "bash -lc 'set -euo pipefail; "
        f"cd {esc_site_dir}; "
        f"printf %s {php_b64} | base64 -d > {esc_remote_php}; "
        f"php {esc_remote_php}; "
        f"rm -f {esc_remote_php}'"
    )
    raw = await execute_remote_cmd(server_ip, int(ssh_port or 22), cmd, timeout_sec=60)
    data = json.loads((raw or "").strip() or "{}")
    if not data.get("database") or not data.get("username"):
        raise Exception("站点数据库配置不完整")
    return data


async def _apply_tdk_to_remote_site(site: Site, server: Server, tdk: TDKConfig):
    cfg = await _load_remote_db_config(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), site.domain)
    prefix = _safe_ident((cfg.get("prefix") or "ey_").strip() or "ey_")
    config_table = _safe_ident(f"{prefix}config")
    db_host = (cfg.get("host") or "127.0.0.1").strip()
    db_port = int(cfg.get("port") or 3306)
    db_name = (cfg.get("database") or "").strip()
    db_user = (cfg.get("username") or "").strip()
    db_pass = str(cfg.get("password") or "")

    seq = int(site.id or 0) * 100
    columns_raw = await _run_remote_mysql(
        server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
        db_host, db_port, db_name, db_user, db_pass,
        f"SHOW COLUMNS FROM {config_table};", seq + 1
    )
    columns = [line.split("\t", 1)[0].strip().strip("`") for line in (columns_raw or "").splitlines() if line.strip()]
    col_set = set(columns)
    if not col_set:
        raise Exception("未读取到配置表字段")

    title = _safe_sql_text(tdk.title)
    keywords = _safe_sql_text(tdk.keywords)
    description = _safe_sql_text(tdk.description)

    def pick_first(candidates):
        for c in candidates:
            if c in col_set:
                return c
        return None

    key_col = pick_first(["name", "config_name", "key"])
    value_col = pick_first(["value", "config_value", "val"])

    if key_col and value_col:
        keys_raw = await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            f"SELECT `{key_col}` FROM {config_table};", seq + 2
        )
        keys = {line.strip() for line in (keys_raw or "").splitlines() if line.strip()}
        title_candidates = [k for k in ["web_title", "web_name", "seo_title", "title"] if k in keys]
        keywords_candidates = [k for k in ["web_keywords", "seo_keywords", "keywords"] if k in keys]
        desc_candidates = [k for k in ["web_description", "seo_description", "description"] if k in keys]
        if not title_candidates or not keywords_candidates or not desc_candidates:
            raise Exception("配置表键名不完整，无法切换TDK")
        q = lambda arr: ", ".join("'" + _safe_sql_text(x) + "'" for x in arr)
        update_sql = (
            f"UPDATE {config_table} SET `{value_col}`='{title}' WHERE `{key_col}` IN ({q(title_candidates)});"
            f"UPDATE {config_table} SET `{value_col}`='{keywords}' WHERE `{key_col}` IN ({q(keywords_candidates)});"
            f"UPDATE {config_table} SET `{value_col}`='{description}' WHERE `{key_col}` IN ({q(desc_candidates)});"
        )
        await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            update_sql, seq + 3
        )
        verify_keys = title_candidates + keywords_candidates + desc_candidates
        verify_sql = (
            f"SELECT `{key_col}`, `{value_col}` FROM {config_table} "
            f"WHERE `{key_col}` IN ({q(verify_keys)});"
        )
        verify_raw = await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            verify_sql, seq + 31
        )
        kv = {}
        for line in (verify_raw or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                kv.setdefault(parts[0].strip(), []).append(parts[1].strip())
        exp_title = str(tdk.title or "")
        exp_keywords = str(tdk.keywords or "")
        exp_desc = str(tdk.description or "")
        if not any(exp_title in kv.get(k, []) for k in title_candidates):
            raise Exception(f"远端校验失败：title 未生效（{','.join(title_candidates)}）")
        if not any(exp_keywords in kv.get(k, []) for k in keywords_candidates):
            raise Exception(f"远端校验失败：keywords 未生效（{','.join(keywords_candidates)}）")
        if not any(exp_desc in kv.get(k, []) for k in desc_candidates):
            raise Exception(f"远端校验失败：description 未生效（{','.join(desc_candidates)}）")
    else:
        title_col = pick_first(["web_title", "web_name", "seo_title", "title"])
        keywords_col = pick_first(["web_keywords", "seo_keywords", "keywords"])
        desc_col = pick_first(["web_description", "seo_description", "description"])
        if not (title_col and keywords_col and desc_col):
            raise Exception("配置表字段结构不兼容，无法切换TDK")
        update_sql = (
            f"UPDATE {config_table} SET `{title_col}`='{title}', "
            f"`{keywords_col}`='{keywords}', "
            f"`{desc_col}`='{description}' LIMIT 1;"
        )
        await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            update_sql, seq + 4
        )
        verify_raw = await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            f"SELECT `{title_col}`, `{keywords_col}`, `{desc_col}` FROM {config_table} LIMIT 1;", seq + 41
        )
        lines = [x for x in (verify_raw or "").splitlines() if x.strip()]
        if not lines:
            raise Exception("远端校验失败：未读到配置内容")
        vals = lines[0].split("\t")
        if len(vals) < 3:
            raise Exception("远端校验失败：配置列不足")
        if vals[0].strip() != str(tdk.title or ""):
            raise Exception("远端校验失败：title 不一致")
        if vals[1].strip() != str(tdk.keywords or ""):
            raise Exception("远端校验失败：keywords 不一致")
        if vals[2].strip() != str(tdk.description or ""):
            raise Exception("远端校验失败：description 不一致")

    # 清理缓存，避免前台继续读取旧配置。
    site_dir = shlex.quote(f"/www/wwwroot/{site.domain}")
    clear_cache_cmd = (
        "bash -lc 'set -euo pipefail; "
        f"rm -rf {site_dir}/runtime/cache/* {site_dir}/runtime/temp/* {site_dir}/data/runtime/* 2>/dev/null || true'"
    )
    await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), clear_cache_cmd, timeout_sec=30)

@router.get("/", response_model=SitePageResponse)
def get_sites(
    server_id: int = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    page_size = min(page_size, 200)

    query = db.query(Site)
    if server_id:
        query = query.filter(Site.server_id == server_id)

    total = query.count()
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    items = query.order_by(Site.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }

@router.delete("/{site_id}")
def delete_site(site_id: int, purge_bt: bool = False, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    if purge_bt:
        server = db.query(Server).filter(Server.id == site.server_id).first()
        if not server:
            raise HTTPException(status_code=400, detail="找不到站点对应服务器，无法执行宝塔删除")

    task_log = create_task_log(
        db,
        task_type="delete_site_batch",
        task_name="删除站点（异步）",
        message=f"已入队：删除站点 {site.domain}" + ("（含宝塔）" if purge_bt else "（仅本地）"),
        detail={"site_ids": [site_id], "purge_bt": bool(purge_bt)},
        status="queued",
    )
    task = process_batch_delete_sites.delay(task_log.id, [site_id], bool(purge_bt))
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="site.delete.submit",
        message=f"提交删除站点任务: {site.domain}" + ("（含宝塔）" if purge_bt else "（仅本地）"),
        detail={"site_id": site_id, "domain": site.domain, "purge_bt": bool(purge_bt), "task_log_id": task_log.id, "task_id": task.id},
    )
    return {
        "status": "success",
        "queued": 1,
        "missing_ids": [],
        "purge_bt": bool(purge_bt),
        "task_log_id": task_log.id,
        "task_id": task.id,
        "message": "删除任务已入队，等待异步执行",
    }


@router.post("/batch-delete")
def batch_delete_sites(payload: SiteBatchDeleteRequest, purge_bt: bool = False, db: Session = Depends(get_db)):
    ids = sorted(set(payload.site_ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="site_ids 不能为空")

    existing_sites = db.query(Site).filter(Site.id.in_(ids)).all()
    existing_ids = [s.id for s in existing_sites]
    if not existing_sites:
        return {"status": "success", "deleted": 0, "missing_ids": ids}
    missing_ids = [sid for sid in ids if sid not in set(existing_ids)]

    if purge_bt:
        server_ids = sorted({s.server_id for s in existing_sites})
        existing_server_ids = {sid for (sid,) in db.query(Server.id).filter(Server.id.in_(server_ids)).all()}
        missing_server_sites = [s.domain for s in existing_sites if s.server_id not in existing_server_ids]
        if missing_server_sites:
            raise HTTPException(status_code=400, detail=f"以下站点找不到对应服务器，无法执行宝塔删除：{', '.join(missing_server_sites[:5])}")

    task_log = create_task_log(
        db,
        task_type="delete_site_batch",
        task_name="批量删除站点（异步）",
        message=f"已入队：批量删除 {len(existing_sites)} 条" + ("（含宝塔）" if purge_bt else "（仅本地）"),
        detail={"site_ids": existing_ids, "missing_ids": missing_ids, "purge_bt": bool(purge_bt)},
        status="queued",
    )
    task = process_batch_delete_sites.delay(task_log.id, existing_ids, bool(purge_bt))
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="site.batch_delete.submit",
        message=f"提交批量删除站点任务: {len(existing_sites)} 条" + ("（含宝塔）" if purge_bt else "（仅本地）"),
        detail={"site_ids": existing_ids, "missing_ids": missing_ids, "purge_bt": bool(purge_bt), "task_log_id": task_log.id, "task_id": task.id},
    )
    return {
        "status": "success",
        "queued": len(existing_sites),
        "missing_ids": missing_ids,
        "purge_bt": bool(purge_bt),
        "task_log_id": task_log.id,
        "task_id": task.id,
        "message": "批量删除任务已入队，等待异步执行",
    }


@router.post("/batch-switch-tdk")
def batch_switch_tdk(payload: SiteBatchSwitchTdkRequest, db: Session = Depends(get_db)):
    ids = sorted(set(payload.site_ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="site_ids 不能为空")

    tdk = db.query(TDKConfig).filter(TDKConfig.id == payload.tdk_id).first()
    if not tdk:
        raise HTTPException(status_code=404, detail="TDK方案不存在")

    sites = db.query(Site).filter(Site.id.in_(ids)).all()
    existing_ids = {s.id for s in sites}
    missing_ids = [sid for sid in ids if sid not in existing_ids]
    if not sites:
        return {"status": "success", "queued": 0, "missing_ids": ids}

    task_log = create_task_log(
        db,
        task_type="switch_tdk_batch",
        task_name="批量切换TDK",
        message=f"已入队：{len(sites)} 个站点切换到 {tdk.name}",
        detail={"site_ids": ids, "tdk_id": tdk.id, "tdk_name": tdk.name, "missing_ids": missing_ids},
        status="queued",
    )
    task = process_batch_switch_tdk.delay(task_log.id, ids, tdk.id)
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="site.batch_switch_tdk.submit",
        message=f"提交批量切换TDK：{len(sites)} 个站点 -> {tdk.name}",
        detail={"site_ids": ids, "tdk_id": tdk.id, "task_log_id": task_log.id, "task_id": task.id},
    )
    return {"status": "success", "queued": len(sites), "missing_ids": missing_ids, "task_log_id": task_log.id, "task_id": task.id}


@router.post("/batch-enable-https")
def batch_enable_https(payload: SiteBatchHttpsRequest, db: Session = Depends(get_db)):
    ids = sorted(set(payload.site_ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="site_ids 不能为空")
    sites = db.query(Site).filter(Site.id.in_(ids)).all()
    existing_ids = {s.id for s in sites}
    missing_ids = [sid for sid in ids if sid not in existing_ids]
    if not sites:
        return {"status": "success", "queued": 0, "missing_ids": ids}

    task_log = create_task_log(
        db,
        task_type="switch_https_batch",
        task_name="批量配置HTTPS",
        message=f"已入队：{len(sites)} 个站点（Let's Encrypt）",
        detail={"site_ids": ids, "missing_ids": missing_ids, "force_renew": bool(payload.force_renew)},
        status="queued",
    )
    task = process_batch_enable_https.delay(task_log.id, ids, bool(payload.force_renew))
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="site.batch_enable_https.submit",
        message=f"提交批量配置HTTPS：{len(sites)} 个站点",
        detail={"site_ids": ids, "task_log_id": task_log.id, "task_id": task.id, "force_renew": bool(payload.force_renew)},
    )
    return {"status": "success", "queued": len(sites), "missing_ids": missing_ids, "task_log_id": task_log.id, "task_id": task.id}


@router.get("/{site_id}/logs", response_model=list[SiteDeployLogResponse])
def get_site_logs(site_id: int, limit: int = 200, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    limit = max(1, min(limit, 1000))
    logs = db.query(SiteDeployLog).filter(SiteDeployLog.site_id == site_id).order_by(SiteDeployLog.id.desc()).limit(limit).all()
    return logs


@router.websocket("/ws/{site_id}/logs")
async def stream_site_logs(websocket: WebSocket, site_id: int):
    await websocket.accept()
    db = SessionLocal()
    redis_conn = None
    pubsub = None
    try:
        site = db.query(Site).filter(Site.id == site_id).first()
        if not site:
            await websocket.send_json({"type": "error", "message": "站点不存在"})
            return

        redis_conn = redis_async.from_url(REDIS_URL, decode_responses=True)
        pubsub = redis_conn.pubsub()
        await pubsub.subscribe(f"{LOG_CHANNEL_PREFIX}:{site_id}")

        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10.0)
            if msg and msg.get("data"):
                try:
                    payload = json.loads(msg["data"])
                    await websocket.send_json({"type": "log", "data": payload})
                except Exception:
                    # 跳过格式异常消息，继续监听
                    pass
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return
    except Exception:
        # Pub/Sub 异常时回退到 DB 轮询，避免前端无日志
        last_id = 0
        try:
            while True:
                logs = (
                    db.query(SiteDeployLog)
                    .filter(SiteDeployLog.site_id == site_id, SiteDeployLog.id > last_id)
                    .order_by(SiteDeployLog.id.asc())
                    .limit(200)
                    .all()
                )
                for log in logs:
                    await websocket.send_json(
                        {
                            "type": "log",
                            "data": {
                                "id": log.id,
                                "site_id": log.site_id,
                                "level": log.level,
                                "stage": log.stage,
                                "message": log.message,
                                "created_at": log.created_at.isoformat() if log.created_at else None,
                            },
                        }
                    )
                    last_id = log.id
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            return
    finally:
        if pubsub:
            await pubsub.close()
        if redis_conn:
            await redis_conn.close()
        db.close()


@router.post("/cleanup-stuck")
def cleanup_stuck_sites(
    timeout_minutes: int = 60,
    limit: int = 200,
    dry_run: bool = False,
    db: Session = Depends(get_db)
):
    timeout_minutes = max(1, min(timeout_minutes, 10080))
    limit = max(1, min(limit, 2000))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    stuck_sites = (
        db.query(Site)
        .filter(Site.status == "deploying")
        .filter(Site.updated_at < cutoff)
        .order_by(Site.updated_at.asc())
        .limit(limit)
        .all()
    )
    site_ids = [s.id for s in stuck_sites]

    if dry_run:
        return {
            "status": "success",
            "dry_run": True,
            "timeout_minutes": timeout_minutes,
            "matched": len(stuck_sites),
            "site_ids": site_ids
        }

    for site in stuck_sites:
        site.status = "failed"
        if not site.error_msg:
            site.error_msg = f"任务超时未完成（超过 {timeout_minutes} 分钟）"
        db.add(
            SiteDeployLog(
                site_id=site.id,
                level="error",
                stage="timeout",
                message=f"系统自动标记失败：deploying 状态超过 {timeout_minutes} 分钟"
            )
        )

    db.commit()
    log_operation(
        db,
        action="site.cleanup_stuck",
        message=f"清理卡住部署: {len(stuck_sites)} 条",
        detail={"timeout_minutes": timeout_minutes, "marked_failed": len(stuck_sites), "site_ids": site_ids},
    )
    return {
        "status": "success",
        "dry_run": False,
        "timeout_minutes": timeout_minutes,
        "marked_failed": len(stuck_sites),
        "site_ids": site_ids
    }
