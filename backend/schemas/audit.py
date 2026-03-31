from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class OperationLogResponse(BaseModel):
    id: int
    username: Optional[str] = None
    action: str
    message: str
    detail: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True


class TaskLogResponse(BaseModel):
    id: int
    task_type: str
    task_name: str
    status: str
    username: Optional[str] = None
    message: Optional[str] = None
    detail: Optional[str] = None
    task_ref: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        orm_mode = True
