import base64
import json
import re
import shlex

from core.ssh_client import execute_remote_cmd


def _safe_sql_text(value: str) -> str:
    return str(value or "").replace("'", "''")


def _safe_ident(value: str) -> str:
    text = str(value or "").strip()
    if not re.match(r"^[A-Za-z0-9_]+$", text):
        raise Exception(f"非法标识符: {text}")
    return text


async def _run_remote_mysql(
    server_ip: str,
    ssh_port: int,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_pass: str,
    sql: str,
    seq: int,
) -> str:
    sql_path = f"/tmp/autocms_tdk_switch_{seq}.sql"
    esc_sql_path = shlex.quote(sql_path)
    sql_b64 = base64.b64encode(((sql or "").strip() + "\n").encode("utf-8")).decode("ascii")
    cmd = (
        "bash -lc 'set -euo pipefail; "
        f"printf %s {sql_b64} | base64 -d > {esc_sql_path}; "
        f"mysql --connect-timeout=20 -N -h{shlex.quote(db_host)} -P{int(db_port)} "
        f"-u{shlex.quote(db_user)} -p{shlex.quote(db_pass)} {shlex.quote(db_name)} < {esc_sql_path}; "
        f"rm -f {esc_sql_path}'"
    )
    return await execute_remote_cmd(server_ip, int(ssh_port or 22), cmd, timeout_sec=90)


async def _load_remote_db_config(server_ip: str, ssh_port: int, domain: str) -> dict:
    site_dir = f"/www/wwwroot/{domain}"
    esc_site_dir = shlex.quote(site_dir)
    php_script = (
        "<?php\n"
        f"$siteDir = {json.dumps(site_dir, ensure_ascii=False)};\n"
        "$files = [\n"
        "    $siteDir . '/data/conf/database.php',\n"
        "    $siteDir . '/config/database.php',\n"
        "    $siteDir . '/application/database.php',\n"
        "];\n"
        "$cfg = null;\n"
        "foreach ($files as $f) {\n"
        "    if (is_file($f)) {\n"
        "        $tmp = include $f;\n"
        "        if (is_array($tmp)) { $cfg = $tmp; break; }\n"
        "    }\n"
        "}\n"
        "if (!is_array($cfg)) { fwrite(STDERR, 'db config not found'); exit(2); }\n"
        "$result = [\n"
        "    'host' => strval($cfg['hostname'] ?? '127.0.0.1'),\n"
        "    'port' => intval($cfg['hostport'] ?? 3306),\n"
        "    'database' => strval($cfg['database'] ?? ''),\n"
        "    'username' => strval($cfg['username'] ?? ''),\n"
        "    'password' => strval($cfg['password'] ?? ''),\n"
        "    'prefix' => strval($cfg['prefix'] ?? 'ey_'),\n"
        "];\n"
        "echo json_encode($result, JSON_UNESCAPED_UNICODE);\n"
    )
    php_b64 = base64.b64encode(php_script.encode("utf-8")).decode("ascii")
    remote_php = f"/tmp/autocms_dbcfg_{domain.replace('.', '_')}.php"
    esc_remote_php = shlex.quote(remote_php)
    cmd = (
        "bash -lc 'set -euo pipefail; "
        f"cd {esc_site_dir}; "
        f"printf %s {php_b64} | base64 -d > {esc_remote_php}; "
        f"php {esc_remote_php}; "
        f"rm -f {esc_remote_php}'"
    )
    raw = await execute_remote_cmd(server_ip, int(ssh_port or 22), cmd, timeout_sec=60)
    data = json.loads((raw or "").strip() or "{}")
    if not data.get("database") or not data.get("username"):
        raise Exception("站点数据库配置不完整")
    return data


async def apply_tdk_to_remote_site(site, server, tdk):
    cfg = await _load_remote_db_config(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), site.domain)
    prefix = _safe_ident((cfg.get("prefix") or "ey_").strip() or "ey_")
    config_table = _safe_ident(f"{prefix}config")
    db_host = (cfg.get("host") or "127.0.0.1").strip()
    db_port = int(cfg.get("port") or 3306)
    db_name = (cfg.get("database") or "").strip()
    db_user = (cfg.get("username") or "").strip()
    db_pass = str(cfg.get("password") or "")

    seq = int(getattr(site, "id", 0) or 0) * 100
    columns_raw = await _run_remote_mysql(
        server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
        db_host, db_port, db_name, db_user, db_pass,
        f"SHOW COLUMNS FROM {config_table};", seq + 1
    )
    columns = [line.split("\t", 1)[0].strip().strip("`") for line in (columns_raw or "").splitlines() if line.strip()]
    col_set = set(columns)
    if not col_set:
        raise Exception("未读取到配置表字段")

    title = _safe_sql_text(getattr(tdk, "title", ""))
    keywords = _safe_sql_text(getattr(tdk, "keywords", ""))
    description = _safe_sql_text(getattr(tdk, "description", ""))

    def pick_first(candidates):
        for c in candidates:
            if c in col_set:
                return c
        return None

    key_col = pick_first(["name", "config_name", "key"])
    value_col = pick_first(["value", "config_value", "val"])

    if key_col and value_col:
        keys_raw = await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            f"SELECT `{key_col}` FROM {config_table};", seq + 2
        )
        keys = {line.strip() for line in (keys_raw or "").splitlines() if line.strip()}
        title_candidates = [k for k in ["web_title", "web_name", "seo_title", "title"] if k in keys]
        keywords_candidates = [k for k in ["web_keywords", "seo_keywords", "keywords"] if k in keys]
        desc_candidates = [k for k in ["web_description", "seo_description", "description"] if k in keys]
        if not title_candidates or not keywords_candidates or not desc_candidates:
            raise Exception("配置表键名不完整，无法切换TDK")
        q = lambda arr: ", ".join("'" + _safe_sql_text(x) + "'" for x in arr)
        update_sql = (
            f"UPDATE {config_table} SET `{value_col}`='{title}' WHERE `{key_col}` IN ({q(title_candidates)});"
            f"UPDATE {config_table} SET `{value_col}`='{keywords}' WHERE `{key_col}` IN ({q(keywords_candidates)});"
            f"UPDATE {config_table} SET `{value_col}`='{description}' WHERE `{key_col}` IN ({q(desc_candidates)});"
        )
        await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            update_sql, seq + 3
        )
        verify_keys = title_candidates + keywords_candidates + desc_candidates
        verify_sql = (
            f"SELECT `{key_col}`, `{value_col}` FROM {config_table} "
            f"WHERE `{key_col}` IN ({q(verify_keys)});"
        )
        verify_raw = await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            verify_sql, seq + 31
        )
        kv = {}
        for line in (verify_raw or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                kv.setdefault(parts[0].strip(), []).append(parts[1].strip())
        exp_title = str(getattr(tdk, "title", "") or "")
        exp_keywords = str(getattr(tdk, "keywords", "") or "")
        exp_desc = str(getattr(tdk, "description", "") or "")
        if not any(exp_title in kv.get(k, []) for k in title_candidates):
            raise Exception(f"远端校验失败：title 未生效（{','.join(title_candidates)}）")
        if not any(exp_keywords in kv.get(k, []) for k in keywords_candidates):
            raise Exception(f"远端校验失败：keywords 未生效（{','.join(keywords_candidates)}）")
        if not any(exp_desc in kv.get(k, []) for k in desc_candidates):
            raise Exception(f"远端校验失败：description 未生效（{','.join(desc_candidates)}）")
    else:
        title_col = pick_first(["web_title", "web_name", "seo_title", "title"])
        keywords_col = pick_first(["web_keywords", "seo_keywords", "keywords"])
        desc_col = pick_first(["web_description", "seo_description", "description"])
        if not (title_col and keywords_col and desc_col):
            raise Exception("配置表字段结构不兼容，无法切换TDK")
        update_sql = (
            f"UPDATE {config_table} SET `{title_col}`='{title}', "
            f"`{keywords_col}`='{keywords}', "
            f"`{desc_col}`='{description}' LIMIT 1;"
        )
        await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            update_sql, seq + 4
        )
        verify_raw = await _run_remote_mysql(
            server.main_ip, int(getattr(server, "ssh_port", 22) or 22),
            db_host, db_port, db_name, db_user, db_pass,
            f"SELECT `{title_col}`, `{keywords_col}`, `{desc_col}` FROM {config_table} LIMIT 1;", seq + 41
        )
        lines = [x for x in (verify_raw or "").splitlines() if x.strip()]
        if not lines:
            raise Exception("远端校验失败：未读到配置内容")
        vals = lines[0].split("\t")
        if len(vals) < 3:
            raise Exception("远端校验失败：配置列不足")
        if vals[0].strip() != str(getattr(tdk, "title", "") or ""):
            raise Exception("远端校验失败：title 不一致")
        if vals[1].strip() != str(getattr(tdk, "keywords", "") or ""):
            raise Exception("远端校验失败：keywords 不一致")
        if vals[2].strip() != str(getattr(tdk, "description", "") or ""):
            raise Exception("远端校验失败：description 不一致")

    site_dir = shlex.quote(f"/www/wwwroot/{site.domain}")
    clear_cache_cmd = (
        "bash -lc 'set -euo pipefail; "
        f"rm -rf {site_dir}/runtime/cache/* {site_dir}/runtime/temp/* {site_dir}/data/runtime/* 2>/dev/null || true'"
    )
    await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), clear_cache_cmd, timeout_sec=30)
