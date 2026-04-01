import base64
import json
import re
import shlex
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from core.ssh_client import execute_remote_cmd
from models.asset import PluginPackage, PluginVersion, SitePluginDeployment
from models.site import Site
from models.server import Server


DEFAULT_PLUGIN_NAME = "跳转守卫插件"
DEFAULT_PLUGIN_TYPE = "redirect"
DEFAULT_PLUGIN_VERSION = "1.0.1"


def default_redirect_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "ip_whitelist": "",
        "redirect_path": "/ldy/",
        "mobile_ua_regex": "android|iphone|ipod|ipad|windows phone|mobile|blackberry|opera mini|ucbrowser|micromessenger",
        "spider_ua_keyword": "baiduspider",
        "allow_baidu_when_whitelisted": True,
        "non_mobile_response_code": 404,
    }


def _normalize_whitelist(text: str) -> str:
    return "\n".join([x.strip() for x in str(text or "").splitlines() if x.strip()])


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_semver(version: str) -> Tuple[int, int, int]:
    m = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$", str(version or ""))
    if not m:
        raise ValueError("版本号格式必须是 x.y.z")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_version_gt(next_version: str, current_version: str) -> bool:
    return parse_semver(next_version) > parse_semver(current_version)


def bump_patch(version: str) -> str:
    major, minor, patch = parse_semver(version)
    return f"{major}.{minor}.{patch + 1}"


def ensure_default_redirect_plugin(db: Session) -> PluginPackage:
    plugin = db.query(PluginPackage).filter(PluginPackage.plugin_type == DEFAULT_PLUGIN_TYPE).order_by(PluginPackage.id.asc()).first()
    if plugin:
        return plugin
    cfg = default_redirect_config()
    plugin = PluginPackage(
        plugin_type=DEFAULT_PLUGIN_TYPE,
        name=DEFAULT_PLUGIN_NAME,
        owner_username="system",
        current_version=DEFAULT_PLUGIN_VERSION,
        config_json=json.dumps(cfg, ensure_ascii=False),
    )
    db.add(plugin)
    db.commit()
    db.refresh(plugin)
    ver = PluginVersion(
        plugin_id=plugin.id,
        version=DEFAULT_PLUGIN_VERSION,
        change_log="初始化默认跳转插件版本",
        config_snapshot_json=json.dumps(cfg, ensure_ascii=False),
        created_by="system",
    )
    db.add(ver)
    db.commit()
    return plugin


def render_redirect_guard_php(config: Dict[str, Any]) -> str:
    custom_php = str(config.get("php_code") or "").strip()
    if custom_php:
        return custom_php if custom_php.startswith("<?php") else ("<?php\n" + custom_php)
    enabled = _safe_bool(config.get("enabled"), False)
    whitelist = _normalize_whitelist(str(config.get("ip_whitelist") or ""))
    redirect_path = str(config.get("redirect_path") or "/ldy/").strip() or "/ldy/"
    mobile_regex = str(config.get("mobile_ua_regex") or default_redirect_config()["mobile_ua_regex"])
    spider_kw = str(config.get("spider_ua_keyword") or "baiduspider").strip().lower() or "baiduspider"
    allow_baidu = _safe_bool(config.get("allow_baidu_when_whitelisted"), True)
    non_mobile_code = int(config.get("non_mobile_response_code") or 404)
    if non_mobile_code < 100 or non_mobile_code > 599:
        non_mobile_code = 404

    return (
        "<?php\n"
        f"$AUTO_REDIRECT_ENABLED = {'true' if enabled else 'false'};\n"
        "$AUTO_REDIRECT_WHITELIST = <<<'TXT'\n"
        f"{whitelist}\n"
        "TXT;\n"
        f"$AUTO_REDIRECT_PATH = {json.dumps(redirect_path, ensure_ascii=False)};\n"
        f"$AUTO_MOBILE_REGEX = {json.dumps(mobile_regex, ensure_ascii=False)};\n"
        f"$AUTO_SPIDER_KEYWORD = {json.dumps(spider_kw, ensure_ascii=False)};\n"
        f"$AUTO_ALLOW_BAIDU_WHITELIST = {'true' if allow_baidu else 'false'};\n"
        f"$AUTO_NON_MOBILE_STATUS = {non_mobile_code};\n"
        "if (!$AUTO_REDIRECT_ENABLED) { return; }\n"
        "function autocms_real_ip() {\n"
        "  $keys = ['HTTP_X_FORWARDED_FOR','HTTP_CLIENT_IP','HTTP_X_REAL_IP','REMOTE_ADDR'];\n"
        "  foreach ($keys as $k) {\n"
        "    if (empty($_SERVER[$k])) continue;\n"
        "    $raw = trim((string)$_SERVER[$k]);\n"
        "    if ($k === 'HTTP_X_FORWARDED_FOR') {\n"
        "      $parts = explode(',', $raw);\n"
        "      foreach ($parts as $p) {\n"
        "        $p = trim($p);\n"
        "        if (filter_var($p, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) return $p;\n"
        "      }\n"
        "    }\n"
        "    if (filter_var($raw, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) return $raw;\n"
        "  }\n"
        "  return '0.0.0.0';\n"
        "}\n"
        "function autocms_is_mobile($regex) {\n"
        "  $ua = strtolower((string)($_SERVER['HTTP_USER_AGENT'] ?? ''));\n"
        "  if ($ua === '') return false;\n"
        "  return preg_match('/' . str_replace('/', '\\\\/', $regex) . '/i', $ua) === 1;\n"
        "}\n"
        "function autocms_is_spider($kw) {\n"
        "  $ua = strtolower((string)($_SERVER['HTTP_USER_AGENT'] ?? ''));\n"
        "  return $kw !== '' && strpos($ua, strtolower($kw)) !== false;\n"
        "}\n"
        "function autocms_ip2long_safe($ip) {\n"
        "  $v = ip2long($ip);\n"
        "  if ($v === false) return false;\n"
        "  return sprintf('%u', $v);\n"
        "}\n"
        "function autocms_ip_in_rule($ip, $rule) {\n"
        "  $rule = trim($rule);\n"
        "  if ($rule === '') return false;\n"
        "  if (strpos($rule, '/') !== false) {\n"
        "    list($base, $mask) = array_map('trim', explode('/', $rule, 2));\n"
        "    $ipL = autocms_ip2long_safe($ip); $baseL = autocms_ip2long_safe($base);\n"
        "    $mask = intval($mask);\n"
        "    if ($ipL === false || $baseL === false || $mask < 0 || $mask > 32) return false;\n"
        "    $maskBin = $mask === 0 ? 0 : ((~0 << (32 - $mask)) & 0xFFFFFFFF);\n"
        "    return ((intval($ipL) & $maskBin) === (intval($baseL) & $maskBin));\n"
        "  }\n"
        "  if (strpos($rule, '-') !== false) {\n"
        "    list($a, $b) = array_map('trim', explode('-', $rule, 2));\n"
        "    if (filter_var($a, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) && filter_var($b, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {\n"
        "      $ipL = autocms_ip2long_safe($ip); $aL = autocms_ip2long_safe($a); $bL = autocms_ip2long_safe($b);\n"
        "      if ($ipL === false || $aL === false || $bL === false) return false;\n"
        "      return intval($ipL) >= intval($aL) && intval($ipL) <= intval($bL);\n"
        "    }\n"
        "    if (preg_match('/^((?:\\d{1,3}\\.){3})(\\d{1,3})-(\\d{1,3})$/', $rule, $m)) {\n"
        "      $start = intval($m[2]); $end = intval($m[3]);\n"
        "      if ($start < 0 || $end > 255 || $start > $end) return false;\n"
        "      for ($i = $start; $i <= $end; $i++) {\n"
        "        if ($ip === $m[1] . strval($i)) return true;\n"
        "      }\n"
        "      return false;\n"
        "    }\n"
        "    return false;\n"
        "  }\n"
        "  return $ip === $rule;\n"
        "}\n"
        "function autocms_ip_whitelisted($ip, $rulesText) {\n"
        "  $lines = preg_split('/\\r?\\n/', (string)$rulesText);\n"
        "  foreach ($lines as $line) {\n"
        "    if (autocms_ip_in_rule($ip, trim($line))) return true;\n"
        "  }\n"
        "  return false;\n"
        "}\n"
        "$ip = autocms_real_ip();\n"
        "$isSpider = autocms_is_spider($AUTO_SPIDER_KEYWORD);\n"
        "$inWhite = autocms_ip_whitelisted($ip, $AUTO_REDIRECT_WHITELIST);\n"
        "if ($AUTO_ALLOW_BAIDU_WHITELIST && $isSpider && $inWhite) { return; }\n"
        "$uri = (string)($_SERVER['REQUEST_URI'] ?? '/');\n"
        "if (strpos($uri, $AUTO_REDIRECT_PATH) === 0) { return; }\n"
        "$isMobile = autocms_is_mobile($AUTO_MOBILE_REGEX);\n"
        "if (!$isSpider && !$inWhite && $isMobile) {\n"
        "  header('Location: ' . $AUTO_REDIRECT_PATH, true, 302);\n"
        "  exit;\n"
        "}\n"
        "http_response_code($AUTO_NON_MOBILE_STATUS);\n"
        "exit((string)$AUTO_NON_MOBILE_STATUS);\n"
    )


async def ensure_site_bootstrap(server: Server, domain: str):
    site_dir = f"/www/wwwroot/{domain}"
    patch_script = (
        "import os\n"
        f"site_dir = {json.dumps(site_dir)}\n"
        "idx = os.path.join(site_dir, 'index.php')\n"
        "marker = '/* AUTOCMS_REDIRECT_GUARD */'\n"
        "line = \"if (is_file(__DIR__ . '/redirect_guard.php')) { require __DIR__ . '/redirect_guard.php'; }\"\n"
        "if not os.path.isfile(idx):\n"
        "    raise SystemExit('index.php not found')\n"
        "with open(idx, 'r', encoding='utf-8', errors='ignore') as f:\n"
        "    c = f.read()\n"
        "if marker not in c:\n"
        "    if c.startswith('<?php'):\n"
        "        p = c.find('\\n')\n"
        "        c = c[:p+1] + marker + '\\n' + line + '\\n' + c[p+1:]\n"
        "    else:\n"
        "        c = '<?php\\n' + marker + '\\n' + line + '\\n?>\\n' + c\n"
        "with open(idx, 'w', encoding='utf-8') as f:\n"
        "    f.write(c)\n"
    )
    patch_b64 = base64.b64encode(patch_script.encode("utf-8")).decode("ascii")
    cmd = (
        "bash -lc 'set -euo pipefail; "
        "patch_py=\"/tmp/autocms_redirect_patch.py\"; "
        f"printf %s {patch_b64} | base64 -d > \"$patch_py\"; "
        "pybin=/www/server/panel/pyenv/bin/python3.7; "
        "[ -x \"$pybin\" ] || pybin=/www/server/panel/pyenv/bin/python3; "
        "[ -x \"$pybin\" ] || pybin=python3; "
        "\"$pybin\" \"$patch_py\"; "
        "rm -f \"$patch_py\"'"
    )
    await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), cmd, timeout_sec=90)


async def deploy_redirect_plugin(site: Site, server: Server, config: Dict[str, Any], version: str):
    site_dir = f"/www/wwwroot/{site.domain}"
    guard_php = render_redirect_guard_php(config)
    guard_b64 = base64.b64encode(guard_php.encode("utf-8")).decode("ascii")
    version_line = f"\\n/* version: {version} */\\n"
    version_b64 = base64.b64encode(version_line.encode("utf-8")).decode("ascii")
    cmd = (
        "bash -lc 'set -euo pipefail; "
        f"site_dir={shlex.quote(site_dir)}; "
        "guard=\"$site_dir/redirect_guard.php\"; "
        f"printf %s {guard_b64} | base64 -d > \"$guard\"; "
        f"printf %s {version_b64} | base64 -d >> \"$guard\"'"
    )
    await execute_remote_cmd(server.main_ip, int(getattr(server, "ssh_port", 22) or 22), cmd, timeout_sec=60)
    await ensure_site_bootstrap(server, site.domain)


def upsert_site_plugin_deployment(
    db: Session,
    site_id: int,
    plugin_id: int,
    version: str,
    enabled: bool,
    status: str = "success",
    error_msg: Optional[str] = None,
    task_log_id: Optional[int] = None,
):
    row = (
        db.query(SitePluginDeployment)
        .filter(SitePluginDeployment.site_id == site_id, SitePluginDeployment.plugin_id == plugin_id)
        .first()
    )
    if not row:
        row = SitePluginDeployment(site_id=site_id, plugin_id=plugin_id, version=version, enabled=1 if enabled else 0)
    row.version = str(version or DEFAULT_PLUGIN_VERSION)
    row.enabled = 1 if enabled else 0
    row.status = str(status or "success")
    row.error_msg = error_msg
    row.deploy_task_log_id = task_log_id
    db.add(row)
    return row
