from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from core.database import Base

class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String, unique=True, index=True, comment="站点域名")
    bind_ip = Column(String, comment="绑定的IP")
    server_id = Column(Integer, ForeignKey("servers.id"), comment="所属服务器ID")
    
    template_key = Column(String, comment="使用的模板OBS路径")
    tdk_name = Column(String, nullable=True, comment="使用的TDK方案名")
    tdk_title = Column(String, comment="配置的TDK标题")
    tdk_keywords = Column(String, nullable=True, comment="配置的TDK关键词")
    tdk_description = Column(Text, nullable=True, comment="配置的TDK描述")
    admin_path = Column(String, comment="后台路径")
    admin_username = Column(String, nullable=True, comment="后台账号")
    admin_password = Column(String, nullable=True, comment="后台密码（明文展示）")
    https_enabled = Column(Boolean, default=False, nullable=False, comment="是否启用HTTPS")
    https_auto_renew = Column(Boolean, default=True, nullable=False, comment="是否自动续期")
    https_expire_at = Column(DateTime(timezone=True), nullable=True, comment="HTTPS证书过期时间")
    https_updated_at = Column(DateTime(timezone=True), nullable=True, comment="HTTPS状态更新时间")
    
    status = Column(String, default="deploying", comment="状态: deploying, success, failed")
    error_msg = Column(Text, nullable=True, comment="失败时的错误信息")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
