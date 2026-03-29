from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # JWT 鉴权
    secret_key: str = "dev_secret_key"
    access_token_expire_minutes: int = 1440
    
    # 华为云 OBS
    obs_endpoint: str = ""
    obs_region: str = "ap-southeast-1"
    obs_ak: str = ""
    obs_sk: str = ""
    obs_bucket: str = ""
    
    # SSH
    ssh_private_key_path: str = ""
    
    # 数据库链接 (默认使用本地 SQLite 方便 Mac 调试)
    database_url: str = "sqlite:///./local_dev.db"

    class Config:
        env_file = ".env"
        extra = "ignore" # 容错机制：忽略多余的环境变量

# 全局实例化单例
settings = Settings()