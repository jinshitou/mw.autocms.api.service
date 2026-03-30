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
            try:
                response = await client.post(url, data=payload)
            except httpx.TimeoutException as exc:
                raise Exception(f"宝塔接口超时: {url} -> {exc}")
            except httpx.HTTPError as exc:
                raise Exception(f"宝塔接口网络异常: {url} -> {exc}")

            if response.status_code != 200:
                snippet = (response.text or "")[:300]
                raise Exception(f"宝塔接口状态码异常: {url} status={response.status_code} body={snippet}")
            try:
                body = response.json()
            except Exception:
                snippet = (response.text or "")[:300]
                raise Exception(f"宝塔接口返回非 JSON: {url} body={snippet}")
            if not self._is_success(body):
                msg = body.get("msg") if isinstance(body, dict) else str(body)
                raise Exception(f"宝塔接口业务失败: {url} msg={msg}")
            return body
            
    async def create_site(self, domain: str, host_headers=None, php_version: str = "74") -> dict:
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

        data = {
            "webname": json.dumps({
                "domain": full_domains[0],
                "domainlist": full_domains[1:],
                "count": len(full_domains) - 1
            }, ensure_ascii=False),
            "port": "80",
            "site_dir": f"/www/wwwroot/{domain}",
            "type": "PHP",
            "version": php_version,
            "ps": "批量易优API创建"
        }
        return await self._post("/site?action=AddSite", data)

    async def create_database(self, db_name: str, db_user: str, db_pass: str) -> dict:
        data = {"name": db_name, "db_user": db_user, "password": db_pass, "address": "127.0.0.1"}
        return await self._post("/database?action=AddDatabase", data)
