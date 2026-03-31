import time
import hashlib
import httpx
import json

class BaotaAPI:
    def __init__(self, panel_url: str, api_key: str):
        self.panel_url = panel_url.rstrip('/')
        self.api_key = api_key
    
    def _get_auth_data(self) -> dict:
        request_time = str(int(time.time()))
        md5_key = hashlib.md5(self.api_key.encode('utf-8')).hexdigest()
        request_token = hashlib.md5((request_time + md5_key).encode('utf-8')).hexdigest()
        return {"request_time": request_time, "request_token": request_token}

    @staticmethod
    def _is_success(resp: dict) -> bool:
        if not isinstance(resp, dict):
            return False
        if "status" in resp:
            status = str(resp.get("status")).lower()
            return status in {"1", "true", "ok", "success"}
        # 宝塔某些接口只返回 msg/数据，保守认为是成功
        return True

    async def _post(self, endpoint: str, data: dict = None) -> dict:
        payload = self._get_auth_data()
        if data: payload.update(data)
        timeout = httpx.Timeout(connect=8.0, read=20.0, write=20.0, pool=8.0)
        async with httpx.AsyncClient(verify=False, timeout=timeout, trust_env=False) as client:
            url = f"{self.panel_url}{endpoint}"

            async def _do_post(target_url: str):
                try:
                    return await client.post(target_url, data=payload)
                except httpx.TimeoutException as exc:
                    raise Exception(f"宝塔接口超时: {target_url} -> {exc}")
                except httpx.HTTPError as exc:
                    raise Exception(f"宝塔接口网络异常: {target_url} -> {exc}")

            response = await _do_post(url)

            if response.status_code != 200:
                try:
                    err_json = response.json()
                    msg = err_json.get("msg") if isinstance(err_json, dict) else str(err_json)
                except Exception:
                    msg = ""
                snippet = (response.text or "")[:500]
                raise Exception(f"宝塔接口状态码异常: {url} status={response.status_code} msg={msg} body={snippet}")
            try:
                body = response.json()
            except Exception:
                snippet = (response.text or "")[:500]
                raise Exception(f"宝塔接口返回非 JSON: {url} body={snippet}")
            if not self._is_success(body):
                msg = body.get("msg") if isinstance(body, dict) else str(body)
                raise Exception(f"宝塔接口业务失败: {url} msg={msg}")
            return body
            
    async def create_site(self, domain: str, host_headers=None, php_version: str = "74", remark: str = "") -> dict:
        host_headers = host_headers or ["@", "www"]
        full_domains = []
        for h in host_headers:
            key = (h or "").strip().lower()
            if key == "@":
                full_domains.append(domain)
            elif key:
                full_domains.append(f"{key}.{domain}")
        full_domains = list(dict.fromkeys(full_domains))
        if not full_domains:
            full_domains = [domain]

        preferred_versions = [php_version, "82", "81", "80", "74", "73", "72"]
        versions = []
        for v in preferred_versions:
            vv = str(v).strip()
            if vv and vv not in versions:
                versions.append(vv)

        last_error = None
        for ver in versions:
            data = {
                "webname": json.dumps({
                    "domain": full_domains[0],
                    "domainlist": full_domains[1:],
                    "count": len(full_domains) - 1
                }, ensure_ascii=False),
                "port": "80",
                # 宝塔 AddSite 标准字段是 path；旧版本有时接收 site_dir，这里同时兼容
                "path": f"/www/wwwroot/{domain}",
                "site_dir": f"/www/wwwroot/{domain}",
                "type_id": "0",
                "type": "PHP",
                "version": ver,
                "ps": (remark or "批量易优API创建")[:120],
                "ftp": "false",
                "sql": "false",
            }
            try:
                return await self._post("/site?action=AddSite", data)
            except Exception as exc:
                last_error = exc
                msg = str(exc)
                if "域名已存在" in msg or "您添加的域名已存在" in msg:
                    # 幂等化：站点已存在视为可继续
                    return {"status": True, "msg": "域名已存在，按幂等继续"}
                if "指定PHP版本不存在" in msg:
                    continue
                raise

        raise Exception(f"宝塔建站失败，已尝试 PHP 版本 {versions}，最后错误: {last_error}")

    async def create_database(self, db_name: str, db_user: str, db_pass: str) -> dict:
        data = {
            "name": db_name,
            "db_user": db_user,
            "password": db_pass,
            "address": "127.0.0.1",
            "codeing": "utf8mb4",
            "ps": "AutoCMS",
        }
        try:
            return await self._post("/database?action=AddDatabase", data)
        except Exception as exc:
            msg = str(exc)
            if "数据库已存在" in msg or "database exists" in msg.lower():
                # 幂等化：数据库已存在视为可继续
                return {"status": True, "msg": "数据库已存在，按幂等继续"}
            raise

    async def delete_site(self, domain: str) -> dict:
        # 宝塔删除站点接口在不同版本参数存在差异，按常见参数逐步尝试。
        candidates = [
            {"id": domain, "webname": domain, "path": f"/www/wwwroot/{domain}", "ftp": "1", "database": "1"},
            {"webname": domain, "path": f"/www/wwwroot/{domain}", "ftp": "1", "database": "1"},
            {"id": domain, "ftp": "1", "database": "1"},
            {"name": domain, "ftp": "1", "database": "1"},
        ]
        last_error = None
        for data in candidates:
            try:
                return await self._post("/site?action=DeleteSite", data)
            except Exception as exc:
                last_error = exc
                msg = str(exc)
                if "不存在" in msg or "not exists" in msg.lower():
                    return {"status": True, "msg": "站点不存在，按幂等继续"}
                continue
        raise Exception(f"宝塔删除站点失败: {last_error}")
