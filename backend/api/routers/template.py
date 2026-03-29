from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from typing import List
import uuid

from core.database import get_db
from core.obs_client import OBSClient
from models.asset import TemplatePackage
from schemas.asset import TemplateResponse

router = APIRouter()
obs_client = OBSClient()

@router.post("/upload", response_model=TemplateResponse)
async def upload_template(
    file: UploadFile = File(...),
    pkg_type: str = Form(...),
    name: str = Form(...),
    db: Session = Depends(get_db)
):
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="只允许上传 .zip")
    if pkg_type not in {"core", "theme"}:
        raise HTTPException(status_code=400, detail="pkg_type 仅支持 core 或 theme")
    
    folder = "eyoucms/core" if pkg_type == "core" else "eyoucms/muban"
    safe_filename = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    obs_key = f"{folder}/{safe_filename}"
    
    file_bytes = await file.read()
    
    # 💡 优化：把同步的 OBS 上传扔到线程池，防止阻塞 FastAPI 导致超时
    try:
        await run_in_threadpool(obs_client.upload_file_bytes, obs_key, file_bytes)
    except Exception as e:
        # 把真实错误返回给前端，便于快速定位配置问题
        raise HTTPException(status_code=500, detail=f"上传 OBS 失败: {e}")
    
    new_template = TemplatePackage(name=name, pkg_type=pkg_type, obs_path=obs_key)
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template

@router.get("/", response_model=List[TemplateResponse])
def get_templates(pkg_type: str = None, db: Session = Depends(get_db)):
    query = db.query(TemplatePackage)
    if pkg_type: query = query.filter(TemplatePackage.pkg_type == pkg_type)
    return query.order_by(TemplatePackage.id.desc()).all()

@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    tpl = db.query(TemplatePackage).filter(TemplatePackage.id == template_id).first()
    if not tpl: raise HTTPException(status_code=404)
    db.delete(tpl)
    db.commit()
    return {"status": "success"}
