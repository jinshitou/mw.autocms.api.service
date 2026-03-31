from worker.celery_app import celery_app
from services.deploy_service import DeployEngine
from core.database import SessionLocal
from core.obs_client import OBSClient
import models.server  # noqa: F401 - 确保 SQLAlchemy 能解析 sites.server_id 外键
from models.site import Site
from models.server import Server
from models.asset import TDKConfig
from models.asset import TemplatePackage
from models.site_log import SiteDeployLog
from services.audit_service import update_task_log
from services.audit_service import log_operation
from services.tdk_switch_service import apply_tdk_to_remote_site
import asyncio
import random
import string
import json
import os
import uuid
import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
LOG_CHANNEL_PREFIX = "site_logs"
_redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
_obs_client = OBSClient()


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