from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from schemas.deploy import DeployRequest
from worker.deploy_tasks import process_single_site
from core.database import get_db
from models.server import Server
from models.site import Site

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
    
    for site_data in request.sites:
        # 1. 检查是否已经存在该域名，如果存在则更新，不存在则创建
        site_record = db.query(Site).filter(Site.domain == site_data.domain).first()
        if not site_record:
            site_record = Site(domain=site_data.domain)
            db.add(site_record)
        
        site_record.bind_ip = site_data.bind_ip
        site_record.server_id = target_server.id
        site_record.template_key = request.template_key
        site_record.tdk_title = request.tdk_config.get("title", "")
        site_record.admin_path = request.admin_path
        site_record.status = "deploying"
        site_record.error_msg = None
        
        db.commit()
        db.refresh(site_record)

        # 2. 发送 Celery 任务，并把 site_record.id 传给任务，以便任务完成后更新状态
        task = process_single_site.delay(
            site_id=site_record.id,
            server_ip=target_server.main_ip,
            domain=site_data.domain,
            bind_ip=site_data.bind_ip,
            core_key=request.core_key,
            template_key=request.template_key,
            tdk_config=request.tdk_config,
            admin_path=request.admin_path,
            bt_url=computed_bt_url,
            bt_key=target_server.bt_key
        )
        task_records.append({"domain": site_data.domain, "task_id": task.id})

    return {
        "status": "success",
        "message": f"成功接收 {len(request.sites)} 个站点的任务！",
        "tasks": task_records
    }
