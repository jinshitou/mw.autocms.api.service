from worker.celery_app import celery_app
from services.deploy_service import DeployEngine
import asyncio
import random
import string

@celery_app.task(bind=True)
def process_single_site(self, server_ip, domain, bind_ip, template_key, tdk_config, admin_path, bt_url, bt_key):
    # 1. 实例化核心上站引擎
    engine = DeployEngine(server_ip=server_ip, bt_url=bt_url, bt_key=bt_key, ssh_port=22)

    # 2. 动态生成宝塔数据库名和密码
    db_name = domain.replace('.', '_')[:10] + "".join(random.choices(string.ascii_lowercase, k=4))
    db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    # 3. 执行真正的部署流水线 (注意这里的参数全部带上了名字)
    try:
        result = asyncio.run(engine.execute_eyoucms_deployment(
            domain=domain, 
            db_name=db_name, 
            db_user=db_name, 
            db_pass=db_pass,
            admin_path=admin_path, 
            tdk_config=tdk_config, 
            tpl_obs_key=template_key
        ))
        return result
    except Exception as e:
        print(f"❌ 部署失败 [{domain}]: {str(e)}")
        raise e