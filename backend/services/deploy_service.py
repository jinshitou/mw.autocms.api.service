import asyncio
from core.bt_api_client import BaotaAPI
from core.obs_client import OBSClient
from core.ssh_client import execute_remote_cmd

class DeployEngine:
    def __init__(self, server_ip, bt_url, bt_key, ssh_port=22):
        self.server_ip = server_ip
        self.ssh_port = ssh_port
        self.bt_api = BaotaAPI(bt_url, bt_key)
        self.obs = OBSClient()

    async def execute_eyoucms_deployment(self, domain, db_name, db_user, db_pass, admin_path, tdk_config, tpl_obs_key):
        """执行单站完整部署流水线"""
        
        # 1. 宝塔 API：建站与建库
        print(f"[{domain}] Step 1: 正在宝塔创建站点和数据库...")
        await self.bt_api.create_site(domain=domain, php_version="74")
        await self.bt_api.create_database(db_name, db_user, db_pass)

        # 2. 获取华为 OBS 私有桶的安全下载链接
        print(f"[{domain}] Step 2: 获取源码与模版授权链接...")
        # 假设 EyouCMS 核心包在 OBS 根目录的 eyoucms_core.zip
        core_url = self.obs.get_presigned_url("eyoucms_core.zip") 
        tpl_url = self.obs.get_presigned_url(tpl_obs_key) # 专员选的模版

        # 3. 组装终极 SSH 自动化命令
        site_dir = f"/www/wwwroot/{domain}"
        ssh_command = f"""
        # 进去网站目录并清理空文件
        cd {site_dir} && rm -rf ./*
        
        # 下载并解压 EyouCMS 核心 (静默模式)
        wget -qO core.zip "{core_url}"
        unzip -qo core.zip
        rm -f core.zip
        
        # 下载并覆盖模版
        wget -qO tpl.zip "{tpl_url}"
        unzip -qo tpl.zip -d ./template/
        rm -f tpl.zip
        
        # 锁定目录权限给宝塔的 www 用户
        chown -R www:www {site_dir}
        
        # 重命名安全后台入口
        mv login.php {admin_path}
        
        # 数据库静默安装与 TDK 注入
        mysql -u{db_user} -p{db_pass} {db_name} -e "
        SOURCE {site_dir}/install/eyoucms.sql;
        UPDATE ey_config SET value='{tdk_config['title']}' WHERE name='web_title';
        UPDATE ey_config SET value='{tdk_config['keywords']}' WHERE name='web_keywords';
        UPDATE ey_config SET value='{tdk_config['description']}' WHERE name='web_description';
        "
        
        # 删掉高危的 install 目录
        rm -rf {site_dir}/install/
        """

        # 4. 通过 SSH 下发指令到站群服务器并执行
        print(f"[{domain}] Step 3: 通过 SSH 执行下载、解压与数据库注入...")
        await execute_remote_cmd(self.server_ip, self.ssh_port, ssh_command)
        
        print(f"[{domain}] ✅ 部署大功告成！")
        return {"status": "success", "msg": f"{domain} 部署完成", "admin_url": f"http://{domain}/{admin_path}"}