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
        print(f"[{domain}] Step 1: 宝塔 API 创建环境...")
        await self.bt_api.create_site(domain=domain, php_version="74")
        await self.bt_api.create_database(db_name, db_user, db_pass)

        print(f"[{domain}] Step 2: 获取 OBS 授权下载链接...")
        core_url = self.obs.get_presigned_url("eyoucms_core.zip") 
        tpl_url = self.obs.get_presigned_url(tpl_obs_key)

        print(f"[{domain}] Step 3: 下发 SSH 指令部署系统与 TDK...")
        site_dir = f"/www/wwwroot/{domain}"
        ssh_command = f"""
        cd {site_dir} && rm -rf ./*
        wget -qO core.zip "{core_url}" && unzip -qo core.zip && rm -f core.zip
        wget -qO tpl.zip "{tpl_url}" && unzip -qo tpl.zip -d ./template/ && rm -f tpl.zip
        chown -R www:www {site_dir}
        mv login.php {admin_path}
        
        mysql -u{db_user} -p{db_pass} {db_name} -e "
        SOURCE {site_dir}/install/eyoucms.sql;
        UPDATE ey_config SET value='{tdk_config['title']}' WHERE name='web_title';
        UPDATE ey_config SET value='{tdk_config['keywords']}' WHERE name='web_keywords';
        UPDATE ey_config SET value='{tdk_config['description']}' WHERE name='web_description';
        "
        rm -rf {site_dir}/install/
        """
        await execute_remote_cmd(self.server_ip, self.ssh_port, ssh_command)
        return {"status": "success", "msg": f"{domain} 部署完成"}