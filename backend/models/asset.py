from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from core.database import Base

class TDKConfig(Base):
    __tablename__ = "tdks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, comment="TDK方案名称")
    title = Column(String)
    keywords = Column(String)
    description = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class TemplatePackage(Base):
    __tablename__ = "templates"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, comment="包名称")
    pkg_type = Column(String, comment="类型: core(核心源码) 或 theme(前端模板)")
    obs_path = Column(String, comment="华为云OBS中的完整路径")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
