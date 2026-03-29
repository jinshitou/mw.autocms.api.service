from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import ipaddress
import re
from schemas.deploy import DeployRequest
from worker.deploy_tasks import process_single_site
from core.database import get_db
from models.server import Server
from models.site import Site
from models.site_log import SiteDeployLog

router = APIRouter()

DOMAIN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-zA-Z0-9-]{1,63}\.)+[a-zA-Z]{2,63}$"
)
ADMIN_PATH_REGEX = re.compile(r"^[A-Za-z0-9._-]{3,128}\.php$")


def _is_valid_domain(value: str) -> bool:
    return bool(DOMAIN_REGEX.match((value or "").strip()))


def _is_valid_ipv4(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address((value or "").strip())
        return parsed.version == 4
    except ValueError:
        return False


def _validate_admin_path(admin_path: str) -> bool:
    cleaned = (admin_path or "").strip()
    if "/" in cleaned or "\\" in cleaned:
        return False
    return bool(ADMIN_PATH_REGEX.match(cleaned))

@router.post("/batch")
async def submit_batch_deploy(request: DeployRequest, db: Session = Depends(get_db)):
    if not request.sites:
        raise HTTPException(status_code=400, detail="站点列表不能为空")
    if not (request.core_key or "").strip():
        raise HTTPException(status_code=400, detail="core_key 不能为空")
    if not (request.template_key or "").strip():
        raise HTTPException(status_code=400, detail="template_key 不能为空")
    admin_path = (request.admin_path or "").strip()
    if not _validate_admin_path(admin_path):
        raise HTTPException(status_code=400, detail="admin_path 非法，仅允许文件名格式，如 console_v8x.php")
    required_tdk_fields = {"title", "keywords", "description"}
    if not request.tdk_config or not required_tdk_fields.issubset(set(request.tdk_config.keys())):
        raise HTTPException(status_code=400, detail="tdk_config 缺少必要字段: title/keywords/description")

    target_server = db.query(Server).filter(Server.id == request.server_id).first()
    
    if not target_server:
        raise HTTPException(status_code=404, detail=f"找不到服务器！")
    if not target_server.is_active:
        raise HTTPException(status_code=400, detail="该目标服务器已被停用！")

    computed_bt_url = f"{target_server.bt_protocol}://{target_server.main_ip}:{target_server.bt_port}"
    task_records = []
    seen_domains = set()
    
    for site_data in request.sites:
        domain = (site_data.domain or "").strip().lower()
        bind_ip = (site_data.bind_ip or "").strip()
        if not _is_valid_domain(domain):
            raise HTTPException(status_code=400, detail=f"域名格式不合法: {site_data.domain}")
        if not _is_valid_ipv4(bind_ip):
            raise HTTPException(status_code=400, detail=f"IPv4 格式不合法: {site_data.bind_ip}")
        if domain in seen_domains:
            raise HTTPException(status_code=400, detail=f"请求中存在重复域名: {domain}")
        seen_domains.add(domain)

        # 1. 检查是否已经存在该域名，如果存在则更新，不存在则创建
        site_record = db.query(Site).filter(Site.domain == domain).first()
        if not site_record:
            site_record = Site(domain=domain)
            db.add(site_record)
        
        site_record.bind_ip = bind_ip
        site_record.server_id = target_server.id
        site_record.template_key = request.template_key
        site_record.tdk_title = request.tdk_config.get("title", "")
        site_record.admin_path = admin_path
        site_record.status = "deploying"
        site_record.error_msg = None
        
        db.commit()
        db.refresh(site_record)
        db.add(SiteDeployLog(site_id=site_record.id, level="info", stage="queue", message="任务已入队，等待 Celery Worker 执行"))
        db.commit()

        # 2. 发送 Celery 任务，并把 site_record.id 传给任务，以便任务完成后更新状态
        task = process_single_site.delay(
            site_id=site_record.id,
            server_ip=target_server.main_ip,
            domain=domain,
            bind_ip=bind_ip,
            core_key=request.core_key,
            template_key=request.template_key,
            tdk_config=request.tdk_config,
            admin_path=admin_path,
            bt_url=computed_bt_url,
            bt_key=target_server.bt_key
        )
        task_records.append({"domain": domain, "task_id": task.id})

    return {
        "status": "success",
        "message": f"成功接收 {len(request.sites)} 个站点的任务！",
        "tasks": task_records
    }
