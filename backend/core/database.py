from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from core.config import settings

SQLALCHEMY_DATABASE_URL = settings.database_url

# 兼容 Mac 本地 SQLite 测试
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # 生产环境 PostgreSQL
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# FastAPI 依赖注入函数，确保每次请求都有独立的数据库会话，请求结束自动关闭
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()