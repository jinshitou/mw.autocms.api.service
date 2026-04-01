from sqlalchemy import Column, Integer, String, DateTime, Text
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


class LandingPagePackage(Base):
    __tablename__ = "landing_pages"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, comment="落地页名称")
    obs_path = Column(String, comment="华为云OBS中的完整路径")
    remark = Column(Text, nullable=True, comment="备注")
    username = Column(String, nullable=True, comment="上传用户名")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PluginPackage(Base):
    __tablename__ = "plugins"
    id = Column(Integer, primary_key=True, index=True)
    plugin_type = Column(String(64), index=True, nullable=False, comment="插件类型，单插件模式固定 redirect")
    name = Column(String, index=True, nullable=False, comment="插件名称")
    owner_username = Column(String(64), nullable=True, comment="归属用户")
    current_version = Column(String(32), nullable=False, default="1.0.1", comment="当前版本")
    config_json = Column(Text, nullable=True, comment="插件配置(JSON)")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PluginVersion(Base):
    __tablename__ = "plugin_versions"
    id = Column(Integer, primary_key=True, index=True)
    plugin_id = Column(Integer, nullable=False, index=True, comment="插件ID")
    version = Column(String(32), nullable=False, index=True, comment="版本号")
    change_log = Column(Text, nullable=False, comment="更新说明")
    config_snapshot_json = Column(Text, nullable=True, comment="配置快照(JSON)")
    created_by = Column(String(64), nullable=True, comment="更新人")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class SitePluginDeployment(Base):
    __tablename__ = "site_plugin_deployments"
    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, nullable=False, index=True, comment="站点ID")
    plugin_id = Column(Integer, nullable=False, index=True, comment="插件ID")
    version = Column(String(32), nullable=False, index=True, comment="站点上部署的插件版本")
    enabled = Column(Integer, nullable=False, default=1, comment="是否启用(1/0)")
    status = Column(String(32), nullable=False, default="success", comment="部署状态")
    error_msg = Column(Text, nullable=True, comment="失败原因")
    deploy_task_log_id = Column(Integer, nullable=True, index=True, comment="任务日志ID")
    deployed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
