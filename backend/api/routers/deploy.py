from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import ipaddress
import re
import random
import string
from schemas.deploy import DeployRequest
from worker.deploy_tasks import process_single_site
from core.database import get_db
from models.server import Server
from models.site import Site
from models.site_log import SiteDeployLog
from services.audit_service import create_task_log, update_task_log, log_operation

router = APIRouter()

DOMAIN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-zA-Z0-9-]{1,63}\.)+[a-zA-Z]{2,63}$"
)
ADMIN_PATH_REGEX = re.compile(r"^[A-Za-z0-9._-]{3,128}\.php$")
ALLOWED_HOST_HEADERS = {"@", "www", "m"}


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


def _gen_admin_username(domain: str) -> str:
    seed = re.sub(r"[^a-z0-9]", "", (domain or "").lower())
    if not seed:
        seed = "admin"
    return f"{seed[:8]}{''.join(random.choices(string.digits, k=3))}"


def _gen_admin_password() -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=12))

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
    headers = [h.strip().lower() for h in (request.host_headers or []) if h and h.strip()]
    headers = list(dict.fromkeys(headers))
    if not headers:
        raise HTTPException(status_code=400, detail="host_headers 至少选择一个")
    invalid_headers = [h for h in headers if h not in ALLOWED_HOST_HEADERS]
    if invalid_headers:
        raise HTTPException(status_code=400, detail=f"host_headers 非法: {', '.join(invalid_headers)}")
    retry_limit = int(request.retry_limit or 0)
    if retry_limit < 0 or retry_limit > 5:
        raise HTTPException(status_code=400, detail="retry_limit 仅支持 0-5")
    req_admin_username = (request.admin_username or "").strip()
    req_admin_password = (request.admin_password or "").strip()
    if req_admin_username and not re.match(r"^[A-Za-z0-9_.-]{3,32}$", req_admin_username):
        raise HTTPException(status_code=400, detail="admin_username 非法，仅允许 3-32 位字母数字._-")
    if req_admin_password and len(req_admin_password) < 6:
        raise HTTPException(status_code=400, detail="admin_password 至少 6 位")

    target_server = db.query(Server).filter(Server.id == request.server_id).first()
    
    if not target_server:
        raise HTTPException(status_code=404, detail=f"找不到服务器！")
    if not target_server.is_active:
        raise HTTPException(status_code=400, detail="该目标服务器已被停用！")

    computed_bt_url = f"{target_server.bt_protocol}://{target_server.main_ip}:{target_server.bt_port}"
    task_records = []
    skipped_domains = []
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
        if site_record and site_record.status == "deploying" and not request.force_redeploy:
            skipped_domains.append(domain)
            db.add(
                SiteDeployLog(
                    site_id=site_record.id,
                    level="info",
                    stage="idempotent_skip",
                    message="重复提交已跳过：该域名当前仍在部署中（如需强制重投请开启 force_redeploy）"
                )
            )
            db.commit()
            continue

        if not site_record:
            site_record = Site(domain=domain)
            db.add(site_record)
        
        site_record.bind_ip = bind_ip
        site_record.server_id = target_server.id
        site_record.template_key = request.template_key
        site_record.tdk_name = (request.tdk_name or "").strip() or None
        site_record.tdk_title = request.tdk_config.get("title", "")
        site_record.tdk_keywords = request.tdk_config.get("keywords", "")
        site_record.tdk_description = request.tdk_config.get("description", "")
        site_record.admin_path = admin_path
        site_record.admin_username = req_admin_username or _gen_admin_username(domain)
        site_record.admin_password = req_admin_password or _gen_admin_password()
        site_record.status = "deploying"
        site_record.error_msg = None
        
        db.commit()
        db.refresh(site_record)
        db.add(SiteDeployLog(site_id=site_record.id, level="info", stage="queue", message="任务已入队，等待 Celery Worker 执行"))
        db.commit()

        task_log = create_task_log(
            db,
            task_type="deploy_site",
            task_name="批量上站-单站任务",
            message=f"已入队: {domain}",
            detail={"site_id": site_record.id, "domain": domain, "bind_ip": bind_ip},
            status="queued",
        )

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
            admin_username=site_record.admin_username,
            admin_password=site_record.admin_password,
            host_headers=headers,
            retry_limit=retry_limit,
            task_log_id=task_log.id,
            ssh_port=int(getattr(target_server, "ssh_port", 22) or 22),
            bt_url=computed_bt_url,
            bt_key=target_server.bt_key
        )
        update_task_log(db, task_log.id, task_ref=task.id)
        task_records.append({"domain": domain, "task_id": task.id, "task_log_id": task_log.id})

    accepted_count = len(task_records)
    skipped_count = len(skipped_domains)
    msg_parts = []
    if accepted_count:
        msg_parts.append(f"成功接收 {accepted_count} 个站点任务")
    if skipped_count:
        msg_parts.append(f"跳过 {skipped_count} 个重复部署中站点")
    if not msg_parts:
        msg_parts.append("没有可入队任务（均被幂等保护跳过）")

    log_operation(
        db,
        action="deploy.batch_submit",
        message=f"批量上站提交：接收 {accepted_count} 条，跳过 {skipped_count} 条",
        detail={"server_id": request.server_id, "accepted_count": accepted_count, "skipped_domains": skipped_domains},
        username=None,
    )

    return {
        "status": "success",
        "message": "；".join(msg_parts) + "。",
        "tasks": task_records,
        "accepted_count": accepted_count,
        "skipped_count": skipped_count,
        "skipped_domains": skipped_domains
    }
