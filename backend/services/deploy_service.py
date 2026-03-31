from core.bt_api_client import BaotaAPI
from core.obs_client import OBSClient
from core.ssh_client import execute_remote_cmd
import shlex
import time
import asyncio
import base64
import re
import hashlib
from datetime import datetime

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
        keyword_seed = ""
        keywords_raw = str((tdk_config or {}).get("keywords", "")).strip()
        if keywords_raw:
            keyword_seed = re.split(r"[,，;\s]+", keywords_raw)[0].strip()
        if not keyword_seed:
            keyword_seed = domain.split(".")[0]
        site_remark = f"{datetime.now().strftime('%y-%m-%d %H:%M')} {keyword_seed}"
        print(f"[{domain}] Step 1: 宝塔 API 创建环境...")
        await run_timed_step(
            "bt",
            "宝塔：创建站点",
            self.bt_api.create_site(domain=domain, host_headers=host_headers, php_version="74", remark=site_remark),
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
        core_cache_id = hashlib.md5((core_obs_key or "").encode("utf-8")).hexdigest()[:16]
        tpl_cache_id = hashlib.md5((tpl_obs_key or "").encode("utf-8")).hexdigest()[:16]
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
        php_db_name = db_name.replace("\\", "\\\\").replace("'", "\\'")
        php_db_user = db_user.replace("\\", "\\\\").replace("'", "\\'")
        php_db_pass = db_pass.replace("\\", "\\\\").replace("'", "\\'")
        sql_path = f"/tmp/{db_name}_autocms.sql"
        esc_sql_path = shlex.quote(sql_path)
        mysql_query_seq = {"n": 0}

        async def run_mysql_query(stage, title, sql, timeout_sec=60):
            mysql_query_seq["n"] += 1
            qpath = f"/tmp/{db_name}_autocms_q_{mysql_query_seq['n']}.sql"
            esc_qpath = shlex.quote(qpath)
            sql_text = (sql or "").strip() + "\n"
            sql_b64 = base64.b64encode(sql_text.encode("utf-8")).decode("ascii")
            return await run_timed_step(
                stage,
                title,
                execute_remote_cmd(
                    self.server_ip,
                    self.ssh_port,
                    (
                        "bash -lc 'set -euo pipefail; "
                        f"printf %s {sql_b64} | base64 -d > {esc_qpath}; "
                        f"mysql --connect-timeout=20 -N -u{esc_db_user} -p{esc_db_pass} {esc_db_name} < {esc_qpath}; "
                        f"rm -f {esc_qpath}'"
                    ),
                    timeout_sec=timeout_sec
                ),
                timeout_sec=timeout_sec
            )

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
                (
                    "bash -lc 'set -euo pipefail; "
                    "cache_dir=/www/cache/autocms; "
                    "mkdir -p \"$cache_dir\"; "
                    f"cache_file=\"$cache_dir/core_{core_cache_id}.zip\"; "
                    f"if [ ! -s \"$cache_file\" ]; then wget -nv --timeout=25 --tries=2 -O \"$cache_file\" \"{core_url}\"; fi; "
                    f"cd {esc_site_dir}; cp -f \"$cache_file\" ./core.zip; unzip -qo core.zip; rm -f core.zip'"
                ),
                timeout_sec=300
            )
        )

        await run_timed_step(
            "ssh_tpl",
            "SSH：下载并解压模板包",
            execute_remote_cmd(
                self.server_ip,
                self.ssh_port,
                (
                    "bash -lc 'set -euo pipefail; "
                    "cache_dir=/www/cache/autocms; "
                    "mkdir -p \"$cache_dir\"; "
                    f"cache_file=\"$cache_dir/tpl_{tpl_cache_id}.zip\"; "
                    f"if [ ! -s \"$cache_file\" ]; then wget -nv --timeout=25 --tries=2 -O \"$cache_file\" \"{tpl_url}\"; fi; "
                    f"cd {esc_site_dir}; cp -f \"$cache_file\" ./tpl.zip; unzip -qo tpl.zip -d ./template/; rm -f tpl.zip'"
                ),
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
        table_raw = await run_mysql_query("ssh_admin_probe", "SSH：探测后台管理员表", "SHOW TABLES;", timeout_sec=60)
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

        admin_cols_raw = await run_mysql_query(
            "ssh_admin_probe",
            "SSH：探测管理员表结构",
            f"SHOW COLUMNS FROM {admin_table};",
            timeout_sec=60
        )
        admin_cols = {
            line.split("\t", 1)[0].strip()
            for line in (admin_cols_raw or "").splitlines()
            if line.strip()
        }
        if not admin_cols:
            raise Exception(f"初始化后台账号失败：{admin_table} 无字段信息")
        for c in admin_cols:
            if not re.match(r"^[A-Za-z0-9_]+$", c):
                raise Exception(f"初始化后台账号失败：字段名非法 {c}")

        user_col = pick_first(["user_name", "username", "admin_name", "account", "name"], admin_cols)
        pass_col = pick_first(["password", "passwd", "pwd", "admin_pwd"], admin_cols)
        id_col = pick_first(["admin_id", "id"], admin_cols)
        if not user_col or not pass_col:
            raise Exception(
                f"初始化后台账号失败：管理员表字段不兼容（需账号/密码列），当前字段: {','.join(sorted(admin_cols))}"
            )

        admin_count_raw = await run_mysql_query(
            "ssh_admin_probe",
            "SSH：检查管理员记录数量",
            f"SELECT COUNT(1) FROM {admin_table};",
            timeout_sec=60
        )
        try:
            admin_count = int((admin_count_raw or "0").strip().splitlines()[0])
        except Exception:
            admin_count = 0
        if admin_count <= 0:
            raise Exception(f"初始化后台账号失败：{admin_table} 为空，无法更新管理员")

        order_sql = f" ORDER BY {id_col} ASC" if id_col else ""
        pass_sample_sql = f"SELECT {pass_col} FROM {admin_table}{order_sql} LIMIT 1;"
        pass_sample_raw = await run_mysql_query(
            "ssh_admin_probe",
            "SSH：探测后台密码加密类型",
            pass_sample_sql,
            timeout_sec=60
        )
        pass_sample = (pass_sample_raw or "").strip().splitlines()[0] if (pass_sample_raw or "").strip() else ""
        pass_mode = "md5" if len(pass_sample) == 32 else "bcrypt"

        if pass_mode == "md5":
            auth_code_raw = await run_mysql_query(
                "ssh_admin_probe",
                "SSH：读取 system_auth_code",
                "SELECT `value` FROM ey_config WHERE `name`='system_auth_code' LIMIT 1;",
                timeout_sec=60
            )
            auth_code = (auth_code_raw or "").strip().splitlines()[0] if (auth_code_raw or "").strip() else ""
            if not auth_code:
                raise Exception("初始化后台账号失败：未读取到 system_auth_code")
            esc_auth_code = auth_code.replace("'", "''")
            pass_expr = f"MD5(CONCAT('{esc_auth_code}','{esc_admin_password}'))"
            set_sql = f"{user_col}='{esc_admin_username}', {pass_col}={pass_expr}"
            notify("ssh_admin_probe", "后台密码加密类型：md5(auth_code + password)")
        else:
            crypt_code_raw = await run_mysql_query(
                "ssh_admin_probe",
                "SSH：读取 system_crypt_auth_code",
                "SELECT `value` FROM ey_config WHERE `name`='system_crypt_auth_code' LIMIT 1;",
                timeout_sec=60
            )
            crypt_code = (crypt_code_raw or "").strip().splitlines()[0] if (crypt_code_raw or "").strip() else ""
            if not crypt_code:
                raise Exception("初始化后台账号失败：未读取到 system_crypt_auth_code")
            php_pwd = "'" + admin_password.replace("\\", "\\\\").replace("'", "\\'") + "'"
            php_salt = "'" + crypt_code.replace("\\", "\\\\").replace("'", "\\'") + "'"
            php_script = f"<?php echo crypt({php_pwd}, {php_salt});"
            php_script_b64 = base64.b64encode(php_script.encode("utf-8")).decode("ascii")
            php_path = f"/tmp/{db_name}_autocms_bcrypt.php"
            esc_php_path = shlex.quote(php_path)
            bcrypt_hash_raw = await run_timed_step(
                "ssh_admin_probe",
                "SSH：生成 bcrypt 密码摘要",
                execute_remote_cmd(
                    self.server_ip,
                    self.ssh_port,
                    (
                        "bash -lc 'set -euo pipefail; "
                        f"printf %s {php_script_b64} | base64 -d > {esc_php_path}; "
                        f"php {esc_php_path}; "
                        f"rm -f {esc_php_path}'"
                    ),
                    timeout_sec=60
                ),
                timeout_sec=60
            )
            bcrypt_hash = (bcrypt_hash_raw or "").strip()
            if not bcrypt_hash:
                raise Exception("初始化后台账号失败：bcrypt 摘要生成为空")
            esc_bcrypt_hash = bcrypt_hash.replace("'", "''")
            set_sql = f"{user_col}='{esc_admin_username}', {pass_col}='{esc_bcrypt_hash}'"
            notify("ssh_admin_probe", "后台密码加密类型：bcrypt")

        admin_update_sql = f"UPDATE {admin_table} SET {set_sql}{order_sql} LIMIT 1;"

        await run_mysql_query("ssh_admin_init", "SSH：初始化后台账号密码", admin_update_sql, timeout_sec=90)

        admin_verify_sql = f"SELECT {user_col} FROM {admin_table}{order_sql} LIMIT 1;"
        admin_verify_raw = await run_mysql_query("ssh_admin_verify", "SSH：校验后台账号初始化结果", admin_verify_sql, timeout_sec=60)
        verify_username = (admin_verify_raw or "").strip().splitlines()[0] if (admin_verify_raw or "").strip() else ""
        if verify_username != admin_username:
            raise Exception(f"后台账号初始化校验失败，期望 {admin_username}，实际 {verify_username or '空'}")
        notify("ssh_admin_verify", f"后台账号初始化完成：{admin_username}")

        # 不同 Eyou 版本 ey_config 结构可能不同，先探测再生成 SQL。
        # 注意：按当前业务要求，TDK 注入失败即视为部署失败。
        columns_raw = await run_mysql_query("ssh_tdk_probe", "SSH：探测 ey_config 表结构", "SHOW COLUMNS FROM ey_config;", timeout_sec=60)
        columns = {
            line.split("\t", 1)[0].strip().strip("`")
            for line in (columns_raw or "").splitlines()
            if line.strip()
        }
        if not columns:
            raise Exception("探测 ey_config 失败：未读取到任何字段")

        # 结构 A：key-value 存储（name/value 或兼容命名）
        key_col = pick_first(["name", "config_name", "key"], columns)
        value_col = pick_first(["value", "config_value", "val"], columns)
        tdk_sql_content = ""
        if key_col and value_col:
            keys_raw = await run_mysql_query(
                "ssh_tdk_probe",
                "SSH：探测 ey_config 键名",
                f"SELECT `{key_col}` FROM ey_config;",
                timeout_sec=60
            )
            keys = {line.strip() for line in keys_raw.splitlines() if line.strip()}
            title_candidates = [k for k in ["web_title", "web_name", "seo_title", "title"] if k in keys]
            keywords_candidates = [k for k in ["web_keywords", "seo_keywords", "keywords"] if k in keys]
            desc_candidates = [k for k in ["web_description", "seo_description", "description"] if k in keys]
            if not title_candidates:
                raise Exception("ey_config 缺少 title 对应键（候选：web_title/web_name/seo_title/title）")
            if not keywords_candidates:
                raise Exception("ey_config 缺少 keywords 对应键（候选：web_keywords/seo_keywords/keywords）")
            if not desc_candidates:
                raise Exception("ey_config 缺少 description 对应键（候选：web_description/seo_description/description）")

            def build_where_in(items):
                return ", ".join("'" + str(x).replace("'", "''") + "'" for x in items)

            tdk_sql_content = (
                f"UPDATE ey_config SET `{value_col}`='{esc_title}' WHERE `{key_col}` IN ({build_where_in(title_candidates)});\n"
                f"UPDATE ey_config SET `{value_col}`='{esc_keywords}' WHERE `{key_col}` IN ({build_where_in(keywords_candidates)});\n"
                f"UPDATE ey_config SET `{value_col}`='{esc_desc}' WHERE `{key_col}` IN ({build_where_in(desc_candidates)});\n"
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

        await run_mysql_query("ssh_tdk", "SSH：注入 TDK 配置", tdk_sql_content, timeout_sec=90)

        # 注入后回查，确保 TDK 实际生效；不生效则判定部署失败。
        if key_col and value_col:
            verify_all_keys = []
            for k in title_candidates + keywords_candidates + desc_candidates:
                if k not in verify_all_keys:
                    verify_all_keys.append(k)
            verify_sql = (
                f"SELECT `{key_col}`, `{value_col}` FROM ey_config "
                f"WHERE `{key_col}` IN ({build_where_in(verify_all_keys)});"
            )
            verify_raw = await run_mysql_query("ssh_tdk_verify", "SSH：校验 TDK 注入结果", verify_sql, timeout_sec=60)
            kv = {}
            for line in (verify_raw or "").splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip()
                    kv.setdefault(k, []).append(v)
            exp_title = str(tdk_config.get("title", ""))
            exp_keywords = str(tdk_config.get("keywords", ""))
            exp_desc = str(tdk_config.get("description", ""))
            if not any(exp_title in kv.get(k, []) for k in title_candidates):
                raise Exception(f"TDK 校验失败：title 未生效（候选键: {','.join(title_candidates)}）")
            if not any(exp_keywords in kv.get(k, []) for k in keywords_candidates):
                raise Exception(f"TDK 校验失败：keywords 未生效（候选键: {','.join(keywords_candidates)}）")
            if not any(exp_desc in kv.get(k, []) for k in desc_candidates):
                raise Exception(f"TDK 校验失败：description 未生效（候选键: {','.join(desc_candidates)}）")
            notify("ssh_tdk_verify", "TDK 校验通过（多键兼容）")
        else:
            verify_sql = (
                f"SELECT `{title_col}`, `{keywords_col}`, `{desc_col}` FROM ey_config LIMIT 1;"
            )
            verify_raw = await run_mysql_query("ssh_tdk_verify", "SSH：校验 TDK 注入结果", verify_sql, timeout_sec=60)
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