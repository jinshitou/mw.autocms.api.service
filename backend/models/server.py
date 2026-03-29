from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from core.database import Base

class Server(Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, comment="服务器备注名称")
    main_ip = Column(String, unique=True, index=True, comment="主控IP(用于API通信)")
    ip_pool = Column(Text, comment="可用IP池(逗号分隔)")
    
    bt_protocol = Column(String, default="http")
    bt_port = Column(Integer, default=8888)
    bt_key = Column(String)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
