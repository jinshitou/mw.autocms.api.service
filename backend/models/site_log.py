from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from core.database import Base


class SiteDeployLog(Base):
    __tablename__ = "site_deploy_logs"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"), index=True, nullable=False, comment="站点ID")
    level = Column(String(16), default="info", nullable=False, comment="日志级别")
    stage = Column(String(64), default="system", nullable=False, comment="阶段标识")
    message = Column(Text, nullable=False, comment="日志内容")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
