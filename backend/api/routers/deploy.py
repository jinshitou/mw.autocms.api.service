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
        raise HTTPException(status_code=404, detail=f"找不到 ID 为 {request.server_id} 的服务器！")
    
    if not target_server.is_active:
        raise HTTPException(status_code=400, detail="该目标服务器已被停用！")

    # 💡 核心魔法：系统自动拼接真实的 bt_url，不再需要前端操心
    computed_bt_url = f"{target_server.bt_protocol}://{target_server.ip_address}:{target_server.bt_port}"

    task_records = []
    
    for site in request.sites:
        task = process_single_site.delay(
            server_ip=target_server.ip_address,
            domain=site.domain,
            bind_ip=site.bind_ip,
            template_key=request.template_key,
            tdk_config=request.tdk_config,
            admin_path=request.admin_path,
            bt_url=computed_bt_url,   # 将拼接好的完整 URL 传给后台
            bt_key=target_server.bt_key
        )
        task_records.append({"domain": site.domain, "task_id": task.id})

    return {
        "status": "success",
        "message": f"成功接收 {len(request.sites)} 个站点的任务！",
        "tasks": task_records
    }
