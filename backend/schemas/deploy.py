from pydantic import BaseModel
from typing import List, Dict

class SiteItem(BaseModel):
    domain: str
    bind_ip: str

class DeployRequest(BaseModel):
    server_id: int
    sites: List[SiteItem]
    # 核心改动：分离出核心包和模板包的路径参数
    core_key: str = "eyoucms/eyoucore/EyouCMS-V1.7.8-UTF8-SP1_0125.zip"
    template_key: str = "eyoucms/core/eyouz070.zip"
    
    tdk_config: Dict[str, str] = {
        "title": "2026全新科技企业官网",
        "keywords": "科技,企业,官网",
        "description": "提供最优质的科技资讯与服务。"
    }
    admin_path: str = "console_v8x.php"
