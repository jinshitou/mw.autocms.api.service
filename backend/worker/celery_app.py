from celery import Celery
import os

# 读取环境变量中的 Redis 地址
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery('eyou_tasks', broker=redis_url, backend=redis_url)