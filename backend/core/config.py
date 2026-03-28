from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # JWT
    secret_key: str
    access_token_expire_minutes: int = 1440
    
    # Huawei OBS
    obs_endpoint: str
    obs_ak: str
    obs_sk: str
    obs_bucket: str
    
    # SSH
    ssh_private_key_path: str

    class Config:
        env_file = ".env"

# 全局实例化单例，其他文件直接 from core.config import settings
settings = Settings()