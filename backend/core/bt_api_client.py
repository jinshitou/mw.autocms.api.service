import time
import hashlib
import httpx

class BaotaAPI:
    def __init__(self, panel_url: str, api_key: str):
        self.panel_url = panel_url.rstrip('/')
        self.api_key = api_key
    
    def _get_auth_data(self) -> dict:
        request_time = str(int(time.time()))
        md5_key = hashlib.md5(self.api_key.encode('utf-8')).hexdigest()
        request_token = hashlib.md5((request_time + md5_key).encode('utf-8')).hexdigest()
        return {"request_time": request_time, "request_token": request_token}

    async def _post(self, endpoint: str, data: dict = None) -> dict:
        payload = self._get_auth_data()
        if data: payload.update(data)
        async with httpx.AsyncClient(verify=False) as client:
            url = f"{self.panel_url}{endpoint}"
            response = await client.post(url, data=payload, timeout=30.0)
            return response.json()
            
    async def create_site(self, domain: str, php_version: str = "74") -> dict:
        data = {
            "webname": f'{{"domain": "{domain}", "domainlist": [], "count": 0}}',
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
