from celery import Celery
import os

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery(
    "eyou_tasks",
    broker=redis_url,
    backend=redis_url,
    include=["worker.deploy_tasks"],
)

# 补充自动发现，兼容后续新增模块
celery_app.autodiscover_tasks(["worker"])
