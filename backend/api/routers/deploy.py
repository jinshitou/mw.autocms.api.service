from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from schemas.deploy import DeployRequest
from worker.deploy_tasks import process_single_site
from core.database import get_db
from models.server import Server

router = APIRouter()

@router.post("/batch")
async def submit_batch_deploy(request: DeployRequest, db: Session = Depends(get_db)):
    if not request.sites:
        raise HTTPException(status_code=400, detail="站点列表不能为空")

    target_server = db.query(Server).filter(Server.id == request.server_id).first()
    
    if not target_server:
        raise HTTPException(status_code=404, detail=f"找不到服务器！")
    if not target_server.is_active:
        raise HTTPException(status_code=400, detail="该目标服务器已被停用！")

    computed_bt_url = f"{target_server.bt_protocol}://{target_server.main_ip}:{target_server.bt_port}"
    task_records = []
    
    for site in request.sites:
        task = process_single_site.delay(
            server_ip=target_server.main_ip,
            domain=site.domain,
            bind_ip=site.bind_ip,
            core_key=request.core_key,          # <--- 新增传递核心包路径
            template_key=request.template_key,  # <--- 新增传递模板包路径
            tdk_config=request.tdk_config,
            admin_path=request.admin_path,
            bt_url=computed_bt_url,
            bt_key=target_server.bt_key
        )
        task_records.append({"domain": site.domain, "task_id": task.id})

    return {
        "status": "success",
        "message": f"成功接收 {len(request.sites)} 个站点的任务！(使用模板: {request.template_key})",
        "tasks": task_records
    }
