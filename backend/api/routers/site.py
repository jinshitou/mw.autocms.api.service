from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone

from core.database import get_db
from models.site import Site
from models.site_log import SiteDeployLog
from schemas.site import SitePageResponse, SiteBatchDeleteRequest, SiteDeployLogResponse

router = APIRouter()

@router.get("/", response_model=SitePageResponse)
def get_sites(
    server_id: int = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    page_size = min(page_size, 200)

    query = db.query(Site)
    if server_id:
        query = query.filter(Site.server_id == server_id)

    total = query.count()
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    items = query.order_by(Site.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }

@router.delete("/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    db.query(SiteDeployLog).filter(SiteDeployLog.site_id == site_id).delete(synchronize_session=False)
    db.delete(site)
    db.commit()
    return {"status": "success", "message": "站点记录已删除"}


@router.post("/batch-delete")
def batch_delete_sites(payload: SiteBatchDeleteRequest, db: Session = Depends(get_db)):
    ids = sorted(set(payload.site_ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="site_ids 不能为空")

    existing_ids = [row[0] for row in db.query(Site.id).filter(Site.id.in_(ids)).all()]
    if not existing_ids:
        return {"status": "success", "deleted": 0, "missing_ids": ids}

    db.query(SiteDeployLog).filter(SiteDeployLog.site_id.in_(existing_ids)).delete(synchronize_session=False)
    deleted = db.query(Site).filter(Site.id.in_(existing_ids)).delete(synchronize_session=False)
    db.commit()
    missing_ids = [sid for sid in ids if sid not in set(existing_ids)]
    return {"status": "success", "deleted": deleted, "missing_ids": missing_ids}


@router.get("/{site_id}/logs", response_model=list[SiteDeployLogResponse])
def get_site_logs(site_id: int, limit: int = 200, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    limit = max(1, min(limit, 1000))
    logs = db.query(SiteDeployLog).filter(SiteDeployLog.site_id == site_id).order_by(SiteDeployLog.id.asc()).limit(limit).all()
    return logs


@router.post("/cleanup-stuck")
def cleanup_stuck_sites(
    timeout_minutes: int = 60,
    limit: int = 200,
    dry_run: bool = False,
    db: Session = Depends(get_db)
):
    timeout_minutes = max(1, min(timeout_minutes, 10080))
    limit = max(1, min(limit, 2000))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    stuck_sites = (
        db.query(Site)
        .filter(Site.status == "deploying")
        .filter(Site.updated_at < cutoff)
        .order_by(Site.updated_at.asc())
        .limit(limit)
        .all()
    )
    site_ids = [s.id for s in stuck_sites]

    if dry_run:
        return {
            "status": "success",
            "dry_run": True,
            "timeout_minutes": timeout_minutes,
            "matched": len(stuck_sites),
            "site_ids": site_ids
        }

    for site in stuck_sites:
        site.status = "failed"
        if not site.error_msg:
            site.error_msg = f"任务超时未完成（超过 {timeout_minutes} 分钟）"
        db.add(
            SiteDeployLog(
                site_id=site.id,
                level="error",
                stage="timeout",
                message=f"系统自动标记失败：deploying 状态超过 {timeout_minutes} 分钟"
            )
        )

    db.commit()
    return {
        "status": "success",
        "dry_run": False,
        "timeout_minutes": timeout_minutes,
        "marked_failed": len(stuck_sites),
        "site_ids": site_ids
    }
