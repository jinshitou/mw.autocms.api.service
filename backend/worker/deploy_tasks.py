from worker.celery_app import celery_app
from services.deploy_service import DeployEngine
from core.database import SessionLocal
from models.site import Site
import asyncio
import random
import string

@celery_app.task(bind=True)
def process_single_site(self, site_id, server_ip, domain, bind_ip, core_key, template_key, tdk_config, admin_path, bt_url, bt_key):
    # 1. 实例化核心上站引擎
    engine = DeployEngine(server_ip=server_ip, bt_url=bt_url, bt_key=bt_key, ssh_port=22)

    # 2. 动态生成宝塔数据库名和密码
    db_name = domain.replace('.', '_')[:10] + "".join(random.choices(string.ascii_lowercase, k=4))
    db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    db = SessionLocal()
    try:
        # 3. 执行真正的部署流水线 (注意这里的参数全部带上了名字)
        result = asyncio.run(engine.execute_eyoucms_deployment(
            domain=domain, 
            db_name=db_name, 
            db_user=db_name, 
            db_pass=db_pass,
            admin_path=admin_path, 
            tdk_config=tdk_config, 
            tpl_obs_key=template_key
        ))
        
        # 4. 部署成功，更新数据库状态
        site = db.query(Site).filter(Site.id == site_id).first()
        if site:
            site.status = "success"
            db.commit()
            
        return result
    except Exception as e:
        print(f"❌ 部署失败 [{domain}]: {str(e)}")
        # 5. 部署失败，更新数据库状态和错误信息
        site = db.query(Site).filter(Site.id == site_id).first()
        if site:
            site.status = "failed"
            site.error_msg = str(e)
            db.commit()
        raise e
    finally:
        db.close()