from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class SiteBase(BaseModel):
    domain: str
    bind_ip: str
    server_id: int
    template_key: str
    tdk_title: str
    admin_path: str
    admin_username: Optional[str] = None
    admin_password: Optional[str] = None
    status: str
    error_msg: Optional[str] = None

class SiteResponse(SiteBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class SitePageResponse(BaseModel):
    items: List[SiteResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class SiteBatchDeleteRequest(BaseModel):
    site_ids: List[int] = Field(default_factory=list)


class SiteDeployLogResponse(BaseModel):
    id: int
    site_id: int
    level: str
    stage: str
    message: str
    created_at: datetime

    class Config:
        orm_mode = True
