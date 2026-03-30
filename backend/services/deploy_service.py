from core.bt_api_client import BaotaAPI
from core.obs_client import OBSClient
from core.ssh_client import execute_remote_cmd
import shlex
import time

class DeployEngine:
    def __init__(self, server_ip, bt_url, bt_key, ssh_port=22):
        self.server_ip = server_ip
        self.ssh_port = ssh_port
        self.bt_api = BaotaAPI(bt_url, bt_key)
        self.obs = OBSClient()

    async def execute_eyoucms_deployment(self, domain, db_name, db_user, db_pass, admin_path, tdk_config, core_obs_key, tpl_obs_key, host_headers, on_progress=None):
        def notify(stage, message):
            if callable(on_progress):
                on_progress(stage, message)

        async def run_timed_step(stage, title, coro):
            notify(stage, f"{title} 开始")
            started = time.monotonic()
            result = await coro
            elapsed = time.monotonic() - started
            notify(stage, f"{title} 完成，耗时 {elapsed:.2f}s")
            return result

        total_started = time.monotonic()
        print(f"[{domain}] Step 1: 宝塔 API 创建环境...")
        await run_timed_step(
            "bt",
            "宝塔：创建站点",
            self.bt_api.create_site(domain=domain, host_headers=host_headers, php_version="74")
        )
        await run_timed_step(
            "bt",
            "宝塔：创建数据库",
            self.bt_api.create_database(db_name, db_user, db_pass)
        )

        print(f"[{domain}] Step 2: 获取 OBS 授权下载链接...")
        obs_started = time.monotonic()
        core_url = self.obs.get_presigned_url(core_obs_key)
        tpl_url = self.obs.get_presigned_url(tpl_obs_key)
        notify("obs", f"OBS：生成核心包与模板包临时链接完成，耗时 {time.monotonic() - obs_started:.2f}s")
        if not core_url or not tpl_url:
            raise Exception("OBS 临时下载链接生成失败")

        print(f"[{domain}] Step 3: 下发 SSH 指令部署系统与 TDK...")
        site_dir = f"/www/wwwroot/{domain}"
        esc_site_dir = shlex.quote(site_dir)
        esc_admin_path = shlex.quote(admin_path)
        esc_db_user = shlex.quote(db_user)
        esc_db_pass = shlex.quote(db_pass)
        esc_db_name = shlex.quote(db_name)
        esc_title = str(tdk_config.get("title", "")).replace("'", "''")
        esc_keywords = str(tdk_config.get("keywords", "")).replace("'", "''")
        esc_desc = str(tdk_config.get("description", "")).replace("'", "''")
        sql_path = f"/tmp/{db_name}_autocms.sql"
        esc_sql_path = shlex.quote(sql_path)

        await run_timed_step(
            "ssh_prepare",
            "SSH：清理站点目录",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                f"bash -lc 'set -euo pipefail; mkdir -p {esc_site_dir}; cd {esc_site_dir}; rm -rf ./*'",
                timeout_sec=120
            )
        )

        await run_timed_step(
            "ssh_core",
            "SSH：下载并解压核心包",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                f'bash -lc \'set -euo pipefail; cd {esc_site_dir}; wget -nv --timeout=25 --tries=2 -O core.zip "{core_url}"; unzip -qo core.zip; rm -f core.zip\'',
                timeout_sec=300
            )
        )

        await run_timed_step(
            "ssh_tpl",
            "SSH：下载并解压模板包",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                f'bash -lc \'set -euo pipefail; cd {esc_site_dir}; wget -nv --timeout=25 --tries=2 -O tpl.zip "{tpl_url}"; unzip -qo tpl.zip -d ./template/; rm -f tpl.zip\'',
                timeout_sec=300
            )
        )

        sql_content = (
            f"SOURCE {site_dir}/install/eyoucms.sql;\n"
            f"UPDATE ey_config SET value='{esc_title}' WHERE name='web_title';\n"
            f"UPDATE ey_config SET value='{esc_keywords}' WHERE name='web_keywords';\n"
            f"UPDATE ey_config SET value='{esc_desc}' WHERE name='web_description';\n"
        )
        await run_timed_step(
            "ssh_mysql",
            "SSH：导入数据库并写入 TDK",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"cat > {esc_sql_path} <<\"SQL\"\n{sql_content}SQL\n"
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} < {esc_sql_path}; "
                    f"rm -f {esc_sql_path}'"
                ),
                timeout_sec=180
            )
        )

        await run_timed_step(
            "ssh_finalize",
            "SSH：修复权限并清理安装目录",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"cd {esc_site_dir}; "
                    f"if [ -f login.php ]; then mv login.php {esc_admin_path}; fi; "
                    f"chown -R www:www {esc_site_dir}; "
                    f"rm -rf {esc_site_dir}/install/'"
                ),
                timeout_sec=120
            )
        )
        notify("summary", f"部署全流程完成，总耗时 {time.monotonic() - total_started:.2f}s")
        return {"status": "success", "msg": f"{domain} 部署完成"}