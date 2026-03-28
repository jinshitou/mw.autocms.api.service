from worker.celery_app import celery_app
from services.deploy_service import DeployEngine
import asyncio
import random
import string

@celery_app.task(bind=True)
def process_single_site(self, server_ip, domain, bind_ip, template_key, tdk_config, admin_path):
    """
    后台队列真实执行上站的函数
    """
    # ⚠️ 核心注意：在未来的完整版中，这里的 bt_url 和 bt_key 应该根据 server_ip 去 PostgreSQL 数据库里查
    # 为了当前打通流程，我们先留出参数位（请在这里填入你目标宝塔的真实信息进行测试）
    TARGET_BT_URL = "http://你的目标宝塔IP:8888" 
    TARGET_BT_KEY = "你的宝塔API密钥"
    
    # 实例化部署引擎
    engine = DeployEngine(
        server_ip=server_ip, 
        bt_url=TARGET_BT_URL, 
        bt_key=TARGET_BT_KEY, 
        ssh_port=22
    )

    # 动态生成随机的数据库名和密码 (宝塔要求数据库名最长16位)
    db_name = domain.replace('.', '_')[:10] + "".join(random.choices(string.ascii_lowercase, k=4))
    db_user = db_name
    db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    # 因为 DeployEngine 里的方法是 async 异步的，而 Celery 默认是同步运行的，所以需要用 asyncio 包裹一下
    try:
        result = asyncio.run(engine.execute_eyoucms_deployment(
            domain=domain,
            db_name=db_name,
            db_user=db_user,
            db_pass=db_pass,
            admin_path=admin_path,
            tdk_config=tdk_config,
            tpl_obs_key=template_key
        ))
        return result
    except Exception as e:
        # 任务失败，记录日志 (后续可加上 Telegram 告警)
        print(f"❌ 部署失败 [{domain}]: {str(e)}")
        raise e