from worker.celery_app import celery_app
from services.deploy_service import DeployEngine
from core.database import SessionLocal
from models.site import Site
from models.site_log import SiteDeployLog
import asyncio
import random
import string
import json
import os
import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
LOG_CHANNEL_PREFIX = "site_logs"
_redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


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
def process_single_site(self, site_id, server_ip, domain, bind_ip, core_key, template_key, tdk_config, admin_path, host_headers, retry_limit, bt_url, bt_key):
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
        ]
        if any(k in text for k in non_retryable_keywords):
            return False
        return any(k in text for k in retryable_keywords)

    # 1. 实例化核心上站引擎
    engine = DeployEngine(server_ip=server_ip, bt_url=bt_url, bt_key=bt_key, ssh_port=22)

    # 2. 动态生成宝塔数据库名和密码
    db_name = domain.replace('.', '_')[:10] + "".join(random.choices(string.ascii_lowercase, k=4))
    db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    db = SessionLocal()
    try:
        attempt_no = int(self.request.retries or 0) + 1
        total_attempts = retry_limit + 1
        host_txt = ",".join(host_headers or [])
        _write_log(db, site_id, "start", f"开始部署: {domain} -> {bind_ip}，主机头: {host_txt}，第 {attempt_no}/{total_attempts} 次尝试")
        _write_log(db, site_id, "bt", "正在调用宝塔 API 创建站点与数据库")

        # 3. 执行真正的部署流水线 (注意这里的参数全部带上了名字)
        result = asyncio.run(engine.execute_eyoucms_deployment(
            domain=domain, 
            db_name=db_name, 
            db_user=db_name, 
            db_pass=db_pass,
            admin_path=admin_path, 
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
        raise e
    finally:
        db.close()