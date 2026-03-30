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
def process_single_site(self, site_id, server_ip, domain, bind_ip, core_key, template_key, tdk_config, admin_path, host_headers, bt_url, bt_key):
    # 1. 实例化核心上站引擎
    engine = DeployEngine(server_ip=server_ip, bt_url=bt_url, bt_key=bt_key, ssh_port=22)

    # 2. 动态生成宝塔数据库名和密码
    db_name = domain.replace('.', '_')[:10] + "".join(random.choices(string.ascii_lowercase, k=4))
    db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    db = SessionLocal()
    try:
        host_txt = ",".join(host_headers or [])
        _write_log(db, site_id, "start", f"开始部署: {domain} -> {bind_ip}，主机头: {host_txt}")
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
        # 5. 部署失败，更新数据库状态和错误信息
        site = db.query(Site).filter(Site.id == site_id).first()
        if site:
            site.status = "failed"
            site.error_msg = str(e)
            db.commit()
            _write_log(db, site_id, "error", str(e), "error")
        raise e
    finally:
        db.close()