from pydantic import BaseModel
from typing import List, Dict

class SiteItem(BaseModel):
    domain: str
    bind_ip: str

class DeployRequest(BaseModel):
    server_ip: str
    sites: List[SiteItem]
    template_key: str = "eyoucms_core.zip"
    tdk_config: Dict[str, str] = {
        "title": "默认测试标题",
        "keywords": "测试,默认",
        "description": "这是默认测试描述"
    }
    admin_path: str = "console_admin.php"
    
    # 测试阶段临时接收目标宝塔的凭证
    bt_url: str 
    bt_key: str