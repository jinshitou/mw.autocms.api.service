from pydantic import BaseModel
from datetime import datetime

class ServerBase(BaseModel):
    name: str
    main_ip: str
    ip_pool: str  # 前端传过来的 IP 池字符串
    bt_protocol: str = "http"
    bt_port: int = 8888
    ssh_port: int = 22
    bt_key: str
    is_active: bool = True

class ServerCreate(ServerBase):
    pass


class ServerSshPortUpdate(BaseModel):
    ssh_port: int

class ServerResponse(ServerBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
