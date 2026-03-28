from fastapi import APIRouter, HTTPException
from schemas.deploy import DeployRequest
# 引入下一步我们要写的 Celery 异步任务
from worker.deploy_tasks import process_single_site

router = APIRouter()

@router.post("/batch")
async def submit_batch_deploy(request: DeployRequest):
    """
    接收前端批量上站请求，并推送到 Celery 异步队列
    """
    if not request.sites:
        raise HTTPException(status_code=400, detail="站点列表不能为空")

    task_records = []
    
    # 遍历前端传来的每一个域名，生成独立队列任务
    for site in request.sites:
        # .delay() 是 Celery 的魔法方法，代表“把任务扔进后台排队执行，不要阻塞”
        task = process_single_site.delay(
            server_ip=request.server_ip,
            domain=site.domain,
            bind_ip=site.bind_ip,
            template_key=request.template_key,
            tdk_config=request.tdk_config,
            admin_path=request.admin_path
        )
        task_records.append({"domain": site.domain, "task_id": task.id})

    return {
        "status": "success",
        "message": f"🚀 成功接收 {len(request.sites)} 个站点的自动化部署任务！已进入后台队列排队执行。",
        "tasks": task_records
    }