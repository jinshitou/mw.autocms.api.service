from pydantic import BaseModel
from typing import List, Dict

class SiteItem(BaseModel):
    domain: str
    bind_ip: str

class DeployRequest(BaseModel):
    server_id: int
    sites: List[SiteItem]
    core_key: str
    template_key: str
    tdk_config: Dict[str, str]
    admin_path: str
    admin_username: str = ""
    admin_password: str = ""
    host_headers: List[str] = ["@", "www"]
    force_redeploy: bool = False
    retry_limit: int = 1
