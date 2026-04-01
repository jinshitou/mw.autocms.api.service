from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime


class PluginResponse(BaseModel):
    id: int
    plugin_type: str
    name: str
    owner_username: Optional[str] = None
    current_version: str
    config_json: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PluginVersionResponse(BaseModel):
    id: int
    plugin_id: int
    version: str
    change_log: str
    config_snapshot_json: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PluginSiteItem(BaseModel):
    site_id: int
    domain: str
    server_id: int
    server_name: Optional[str] = None
    server_ip: Optional[str] = None
    plugin_version: str
    enabled: bool
    status: str
    error_msg: Optional[str] = None
    deployed_at: Optional[datetime] = None


class PluginSitePageResponse(BaseModel):
    items: List[PluginSiteItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class PluginUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    owner_username: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    version: str = Field(min_length=5, max_length=32)
    change_log: str = Field(min_length=1, max_length=4000)
    created_by: Optional[str] = None


class PluginUpdateRequest(BaseModel):
    name: Optional[str] = None
    owner_username: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    version: Optional[str] = None
    change_log: Optional[str] = None
    created_by: Optional[str] = None


class PluginRedeployRequest(BaseModel):
    target_mode: Literal["single_site", "single_server", "all_sites"] = "all_sites"
    site_id: Optional[int] = None
    site_ids: List[int] = Field(default_factory=list)
    server_id: Optional[int] = None
    version: Optional[str] = None
