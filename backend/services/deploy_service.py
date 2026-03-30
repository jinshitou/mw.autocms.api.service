from core.bt_api_client import BaotaAPI
from core.obs_client import OBSClient
from core.ssh_client import execute_remote_cmd
import shlex
import time
import asyncio
import base64
import random
import string
import re

class DeployEngine:
    def __init__(self, server_ip, bt_url, bt_key, ssh_port=22):
        self.server_ip = server_ip
        self.ssh_port = ssh_port
        self.bt_api = BaotaAPI(bt_url, bt_key)
        self.obs = OBSClient()

    async def execute_eyoucms_deployment(self, domain, db_name, db_user, db_pass, admin_path, admin_username, admin_password, tdk_config, core_obs_key, tpl_obs_key, host_headers, on_progress=None):
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
        admin_username = (admin_username or "admin").strip()
        admin_password = (admin_password or "").strip()
        if not re.match(r"^[A-Za-z0-9_.-]{3,32}$", admin_username):
            raise Exception("admin_username 非法，仅允许 3-32 位字母数字._-")
        if len(admin_password) < 6:
            raise Exception("admin_password 至少 6 位")
        esc_admin_username = admin_username.replace("'", "''")
        esc_admin_password = admin_password.replace("'", "''")
        admin_salt = "".join(random.choices(string.ascii_letters + string.digits, k=6))
        esc_admin_salt = admin_salt.replace("'", "''")
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
        db_php_config_b64 = base64.b64encode(db_php_config.encode("utf-8")).decode("ascii")
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
                    f"printf %s {db_php_config_b64} | base64 -d > \"$f\"; "
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

        def pick_first(candidates, available):
            for name in candidates:
                if name in available:
                    return name
            return None

        # 初始化后台管理员账号密码（必须成功）。
        table_raw = await run_timed_step(
            "ssh_admin_probe",
            "SSH：探测后台管理员表",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                    "-Nse \"SHOW TABLES;\"'"
                ),
                timeout_sec=60
            )
        )
        table_list = [line.strip() for line in (table_raw or "").splitlines() if line.strip()]
        admin_table = None
        preferred_tables = ["ey_admin", "tp_admin", "admin"]
        for tb in preferred_tables:
            if tb in table_list:
                admin_table = tb
                break
        if not admin_table:
            for tb in table_list:
                if tb.lower().endswith("_admin"):
                    admin_table = tb
                    break
        if not admin_table:
            raise Exception("初始化后台账号失败：未找到管理员表（如 ey_admin）")
        if not re.match(r"^[A-Za-z0-9_]+$", admin_table):
            raise Exception(f"初始化后台账号失败：管理员表名非法 {admin_table}")

        admin_cols_raw = await run_timed_step(
            "ssh_admin_probe",
            "SSH：探测管理员表结构",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                    f"-Nse \"SHOW COLUMNS FROM {admin_table};\" | awk \"{{print \\$1}}\"'"
                ),
                timeout_sec=60
            )
        )
        admin_cols = {line.strip() for line in (admin_cols_raw or "").splitlines() if line.strip()}
        if not admin_cols:
            raise Exception(f"初始化后台账号失败：{admin_table} 无字段信息")
        for c in admin_cols:
            if not re.match(r"^[A-Za-z0-9_]+$", c):
                raise Exception(f"初始化后台账号失败：字段名非法 {c}")

        user_col = pick_first(["user_name", "username", "admin_name", "account", "name"], admin_cols)
        pass_col = pick_first(["password", "passwd", "pwd", "admin_pwd"], admin_cols)
        salt_col = pick_first(["salt", "pwd_salt"], admin_cols)
        id_col = pick_first(["admin_id", "id"], admin_cols)
        if not user_col or not pass_col:
            raise Exception(
                f"初始化后台账号失败：管理员表字段不兼容（需账号/密码列），当前字段: {','.join(sorted(admin_cols))}"
            )

        admin_count_raw = await run_timed_step(
            "ssh_admin_probe",
            "SSH：检查管理员记录数量",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                    f"-Nse \"SELECT COUNT(1) FROM {admin_table};\"'"
                ),
                timeout_sec=60
            )
        )
        try:
            admin_count = int((admin_count_raw or "0").strip().splitlines()[0])
        except Exception:
            admin_count = 0
        if admin_count <= 0:
            raise Exception(f"初始化后台账号失败：{admin_table} 为空，无法更新管理员")

        if salt_col:
            pass_expr = f"MD5(CONCAT(MD5('{esc_admin_password}'),'{esc_admin_salt}'))"
            set_sql = (
                f"{user_col}='{esc_admin_username}', "
                f"{pass_col}={pass_expr}, "
                f"{salt_col}='{esc_admin_salt}'"
            )
        else:
            pass_expr = f"MD5('{esc_admin_password}')"
            set_sql = f"{user_col}='{esc_admin_username}', {pass_col}={pass_expr}"
        order_sql = f" ORDER BY {id_col} ASC" if id_col else ""
        admin_update_sql = f"UPDATE {admin_table} SET {set_sql}{order_sql} LIMIT 1;"

        await run_timed_step(
            "ssh_admin_init",
            "SSH：初始化后台账号密码",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                    f"-Nse \"{admin_update_sql}\"'"
                ),
                timeout_sec=90
            )
        )

        admin_verify_sql = f"SELECT {user_col} FROM {admin_table}{order_sql} LIMIT 1;"
        admin_verify_raw = await run_timed_step(
            "ssh_admin_verify",
            "SSH：校验后台账号初始化结果",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                    f"-Nse \"{admin_verify_sql}\"'"
                ),
                timeout_sec=60
            )
        )
        verify_username = (admin_verify_raw or "").strip().splitlines()[0] if (admin_verify_raw or "").strip() else ""
        if verify_username != admin_username:
            raise Exception(f"后台账号初始化校验失败，期望 {admin_username}，实际 {verify_username or '空'}")
        notify("ssh_admin_verify", f"后台账号初始化完成：{admin_username}")

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

        # 结构 A：key-value 存储（name/value 或兼容命名）
        key_col = pick_first(["name", "config_name", "key"], columns)
        value_col = pick_first(["value", "config_value", "val"], columns)
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
            title_col = pick_first(["web_title", "web_name", "seo_title", "title"], columns)
            keywords_col = pick_first(["web_keywords", "seo_keywords", "keywords"], columns)
            desc_col = pick_first(["web_description", "seo_description", "description"], columns)
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

        # 注入后回查，确保 TDK 实际生效；不生效则判定部署失败。
        if key_col and value_col:
            verify_sql = (
                f"SELECT `{key_col}`, `{value_col}` FROM ey_config "
                f"WHERE `{key_col}` IN ('{title_key}','{keywords_key}','{desc_key}');"
            )
            verify_raw = await run_timed_step(
                "ssh_tdk_verify",
                "SSH：校验 TDK 注入结果",
                execute_remote_cmd(
                    self.server_ip,
                    self.ssh_port,
                    (
                        "bash -lc 'set -euo pipefail; "
                        f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                        f"-Nse \"{verify_sql}\"'"
                    ),
                    timeout_sec=60
                )
            )
            kv = {}
            for line in (verify_raw or "").splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    kv[parts[0].strip()] = parts[1].strip()
            if kv.get(title_key, "") != str(tdk_config.get("title", "")):
                raise Exception(f"TDK 校验失败：{title_key} 未生效")
            if kv.get(keywords_key, "") != str(tdk_config.get("keywords", "")):
                raise Exception(f"TDK 校验失败：{keywords_key} 未生效")
            if kv.get(desc_key, "") != str(tdk_config.get("description", "")):
                raise Exception(f"TDK 校验失败：{desc_key} 未生效")
            notify("ssh_tdk_verify", f"TDK 校验通过：{title_key}/{keywords_key}/{desc_key}")
        else:
            verify_sql = (
                f"SELECT `{title_col}`, `{keywords_col}`, `{desc_col}` FROM ey_config LIMIT 1;"
            )
            verify_raw = await run_timed_step(
                "ssh_tdk_verify",
                "SSH：校验 TDK 注入结果",
                execute_remote_cmd(
                    self.server_ip,
                    self.ssh_port,
                    (
                        "bash -lc 'set -euo pipefail; "
                        f"mysql --connect-timeout=20 -u{esc_db_user} -p{esc_db_pass} {esc_db_name} "
                        f"-Nse \"{verify_sql}\"'"
                    ),
                    timeout_sec=60
                )
            )
            line = (verify_raw or "").strip().splitlines()
            if not line:
                raise Exception("TDK 校验失败：未读到 ey_config 内容")
            vals = line[0].split("\t")
            if len(vals) < 3:
                raise Exception("TDK 校验失败：ey_config 结果列不足")
            if vals[0].strip() != str(tdk_config.get("title", "")):
                raise Exception(f"TDK 校验失败：{title_col} 未生效")
            if vals[1].strip() != str(tdk_config.get("keywords", "")):
                raise Exception(f"TDK 校验失败：{keywords_col} 未生效")
            if vals[2].strip() != str(tdk_config.get("description", "")):
                raise Exception(f"TDK 校验失败：{desc_col} 未生效")
            notify("ssh_tdk_verify", f"TDK 校验通过：{title_col}/{keywords_col}/{desc_col}")

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