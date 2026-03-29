from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class SiteBase(BaseModel):
    domain: str
    bind_ip: str
    server_id: int
    template_key: str
    tdk_title: str
    admin_path: str
    status: str
    error_msg: Optional[str] = None

class SiteResponse(SiteBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
