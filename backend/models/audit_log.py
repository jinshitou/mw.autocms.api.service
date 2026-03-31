from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from core.database import Base


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), nullable=True, comment="操作用户名（预留）")
    action = Column(String(128), nullable=False, comment="操作类型")
    message = Column(Text, nullable=False, comment="操作摘要")
    detail = Column(Text, nullable=True, comment="操作详情(JSON文本)")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_type = Column(String(64), nullable=False, index=True, comment="任务类型")
    task_name = Column(String(128), nullable=False, comment="任务名称")
    status = Column(String(32), nullable=False, default="queued", index=True, comment="queued/running/success/failed")
    username = Column(String(64), nullable=True, comment="触发用户（预留）")
    message = Column(Text, nullable=True, comment="状态摘要")
    detail = Column(Text, nullable=True, comment="任务详情(JSON文本)")
    task_ref = Column(String(128), nullable=True, comment="外部任务ID（如Celery task id）")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
