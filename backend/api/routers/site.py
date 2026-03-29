from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from core.database import get_db
from models.site import Site
from schemas.site import SiteResponse

router = APIRouter()

@router.get("/", response_model=List[SiteResponse])
def get_sites(server_id: int = None, db: Session = Depends(get_db)):
    query = db.query(Site)
    if server_id:
        query = query.filter(Site.server_id == server_id)
    return query.order_by(Site.id.desc()).all()

@router.delete("/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    db.delete(site)
    db.commit()
    return {"status": "success", "message": "站点记录已删除"}
