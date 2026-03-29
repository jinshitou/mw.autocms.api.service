from pydantic import BaseModel
from typing import List, Dict

class SiteItem(BaseModel):
    domain: str
    bind_ip: str

class DeployRequest(BaseModel):
    server_id: int  # <-- 核心改动：前端现在只传服务器 ID
    sites: List[SiteItem]
    template_key: str = "eyoucms_core.zip"
    tdk_config: Dict[str, str] = {
        "title": "2026全新科技企业官网",
        "keywords": "科技,企业,官网",
        "description": "提供最优质的科技资讯与服务。"
    }
    admin_path: str = "console_v8x.php"
