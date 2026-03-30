from core.bt_api_client import BaotaAPI
from core.obs_client import OBSClient
from core.ssh_client import execute_remote_cmd
import shlex
import time
import asyncio

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

        async def run_timed_step(stage, title, coro, timeout_sec=None):
            notify(stage, f"{title} 开始")
            started = time.monotonic()
            try:
                if timeout_sec and timeout_sec > 0:
                    result = await asyncio.wait_for(coro, timeout=timeout_sec)
                else:
                    result = await coro
            except asyncio.TimeoutError:
                raise Exception(f"{title} 超时（>{timeout_sec}s）")
            elapsed = time.monotonic() - started
            notify(stage, f"{title} 完成，耗时 {elapsed:.2f}s")
            return result

        total_started = time.monotonic()
        print(f"[{domain}] Step 1: 宝塔 API 创建环境...")
        await run_timed_step(
            "bt",
            "宝塔：创建站点",
            self.bt_api.create_site(domain=domain, host_headers=host_headers, php_version="74"),
            timeout_sec=45
        )
        await run_timed_step(
            "bt",
            "宝塔：创建数据库",
            self.bt_api.create_database(db_name, db_user, db_pass),
            timeout_sec=45
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
        php_db_name = db_name.replace("\\", "\\\\").replace("'", "\\'")
        php_db_user = db_user.replace("\\", "\\\\").replace("'", "\\'")
        php_db_pass = db_pass.replace("\\", "\\\\").replace("'", "\\'")
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

        sql_content = f"SOURCE {site_dir}/install/eyoucms.sql;\n"
        await run_timed_step(
            "ssh_mysql",
            "SSH：导入数据库基础 SQL",
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

        db_php_config = (
            "<?php\n"
            "return [\n"
            "    'type' => 'mysql',\n"
            "    'hostname' => '127.0.0.1',\n"
            "    'database' => '{db_name}',\n"
            "    'username' => '{db_user}',\n"
            "    'password' => '{db_pass}',\n"
            "    'hostport' => '3306',\n"
            "    'charset' => 'utf8',\n"
            "    'prefix' => 'ey_',\n"
            "];\n"
        ).format(db_name=php_db_name, db_user=php_db_user, db_pass=php_db_pass)
        await run_timed_step(
            "ssh_db_config",
            "SSH：写入数据库连接配置",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    "updated=0; "
                    "for f in "
                    f"{esc_site_dir}/data/conf/database.php "
                    f"{esc_site_dir}/config/database.php "
                    f"{esc_site_dir}/application/database.php; do "
                    "if [ -f \"$f\" ]; then "
                    "cp -f \"$f\" \"$f.bak_autocms\"; "
                    f"cat > \"$f\" <<\"PHP\"\n{db_php_config}PHP\n"
                    "updated=1; "
                    "fi; "
                    "done; "
                    "if [ \"$updated\" -ne 1 ]; then "
                    "echo \"未找到数据库配置文件（data/conf or config or application）\"; "
                    "exit 1; "
                    "fi'"
                ),
                timeout_sec=90
            )
        )

        # 不同 Eyou 版本 ey_config 结构可能不同，先探测再生成 SQL。
        # 注意：按当前业务要求，TDK 注入失败即视为部署失败。
        tdk_sql_path = f"/tmp/{db_name}_autocms_tdk.sql"
        esc_tdk_sql_path = shlex.quote(tdk_sql_path)
        columns_raw = await run_timed_step(
            "ssh_tdk_probe",
            "SSH：探测 ey_config 表结构",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                    "-Nse \"SHOW COLUMNS FROM ey_config;\" | awk \"{print \\$1}\"'"
                ),
                timeout_sec=60
            )
        )
        columns = {line.strip().strip("`") for line in columns_raw.splitlines() if line.strip()}
        if not columns:
            raise Exception("探测 ey_config 失败：未读取到任何字段")

        def pick_first(candidates):
            for name in candidates:
                if name in columns:
                    return name
            return None

        # 结构 A：key-value 存储（name/value 或兼容命名）
        key_col = pick_first(["name", "config_name", "key"])
        value_col = pick_first(["value", "config_value", "val"])
        tdk_sql_content = ""
        if key_col and value_col:
            keys_raw = await run_timed_step(
                "ssh_tdk_probe",
                "SSH：探测 ey_config 键名",
                execute_remote_cmd(
                    self.server_ip,
                    self.ssh_port,
                    (
                        "bash -lc 'set -euo pipefail; "
                        f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                        f"-Nse \"SELECT \\`{key_col}\\` FROM ey_config;\"'"
                    ),
                    timeout_sec=60
                )
            )
            keys = {line.strip() for line in keys_raw.splitlines() if line.strip()}

            def pick_key(candidates, field_name):
                for item in candidates:
                    if item in keys:
                        return item
                raise Exception(f"ey_config 缺少 {field_name} 对应键（候选：{', '.join(candidates)}）")

            title_key = pick_key(["web_title", "web_name", "seo_title", "title"], "title")
            keywords_key = pick_key(["web_keywords", "seo_keywords", "keywords"], "keywords")
            desc_key = pick_key(["web_description", "seo_description", "description"], "description")
            tdk_sql_content = (
                f"UPDATE ey_config SET `{value_col}`='{esc_title}' WHERE `{key_col}`='{title_key}';\n"
                f"UPDATE ey_config SET `{value_col}`='{esc_keywords}' WHERE `{key_col}`='{keywords_key}';\n"
                f"UPDATE ey_config SET `{value_col}`='{esc_desc}' WHERE `{key_col}`='{desc_key}';\n"
            )
        else:
            # 结构 B：直接字段存储（web_title/web_keywords/web_description 等）
            title_col = pick_first(["web_title", "web_name", "seo_title", "title"])
            keywords_col = pick_first(["web_keywords", "seo_keywords", "keywords"])
            desc_col = pick_first(["web_description", "seo_description", "description"])
            if not (title_col and keywords_col and desc_col):
                raise Exception(
                    "ey_config 字段结构不受支持，缺少 TDK 字段（需要 title/keywords/description 对应列）"
                )
            tdk_sql_content = (
                f"UPDATE ey_config SET `{title_col}`='{esc_title}', "
                f"`{keywords_col}`='{esc_keywords}', "
                f"`{desc_col}`='{esc_desc}' LIMIT 1;\n"
            )

        await run_timed_step(
            "ssh_tdk",
            "SSH：注入 TDK 配置",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"cat > {esc_tdk_sql_path} <<\"SQL\"\n{tdk_sql_content}SQL\n"
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} < {esc_tdk_sql_path}; "
                    f"rm -f {esc_tdk_sql_path}'"
                ),
                timeout_sec=90
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
                    f"chown -R www:www {esc_site_dir} 2>/dev/null || true; "
                    f"rm -rf {esc_site_dir}/install/'"
                ),
                timeout_sec=120
            )
        )
        notify("summary", f"部署全流程完成，总耗时 {time.monotonic() - total_started:.2f}s")
        return {"status": "success", "msg": f"{domain} 部署完成"}