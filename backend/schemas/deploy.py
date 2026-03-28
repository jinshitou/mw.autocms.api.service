from pydantic import BaseModel
from typing import List, Dict

# 单个站点的信息 (域名和绑定的IP)
class SiteItem(BaseModel):
    domain: str
    bind_ip: str

# 整个批量上站表单的数据结构
class DeployRequest(BaseModel):
    server_ip: str
    sites: List[SiteItem]
    template_key: str = "eyoucms_core.zip" # 华为 OBS 里的模版文件名
    tdk_config: Dict[str, str] = {
        "title": "2026全新科技企业官网",
        "keywords": "科技,企业,官网",
        "description": "提供最优质的科技资讯与服务。"
    }
    admin_path: str = "console_v8x.php" # 安全后台路径