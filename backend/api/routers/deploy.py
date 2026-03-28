from fastapi import APIRouter, HTTPException
from schemas.deploy import DeployRequest
from worker.deploy_tasks import process_single_site

router = APIRouter()

@router.post("/batch")
async def submit_batch_deploy(request: DeployRequest):
    if not request.sites:
        raise HTTPException(status_code=400, detail="站点列表不能为空")

    task_records = []
    for site in request.sites:
        task = process_single_site.delay(
            server_ip=request.server_ip,
            domain=site.domain,
            bind_ip=site.bind_ip,
            template_key=request.template_key,
            tdk_config=request.tdk_config,
            admin_path=request.admin_path,
            bt_url=request.bt_url,
            bt_k)
        task_records.append({"domain": site.domain, "task_id": task.id})

    return {
        "status": "success",
        "message": f"成功接收 {len(request.sites)} 个站点的部署任务！已进入 Celery 队列。",
        "tasks": task_records
    }
