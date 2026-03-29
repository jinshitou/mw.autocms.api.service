from pydantic import BaseModel
from datetime import datetime

class TDKBase(BaseModel):
    name: str
    title: str
    keywords: str
    description: str

class TDKCreate(TDKBase): pass
class TDKResponse(TDKBase):
    id: int
    created_at: datetime
    class Config: from_attributes = True

class TemplateResponse(BaseModel):
    id: int
    name: str
    pkg_type: str
    obs_path: str
    created_at: datetime
    class Config: from_attributes = True
