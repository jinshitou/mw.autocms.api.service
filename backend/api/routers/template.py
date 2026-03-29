from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
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
    pkg_type: str = Form(..., description="core 或 theme"),
    name: str = Form(...),
    db: Session = Depends(get_db)
):
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="只允许上传 .zip 格式的压缩包")
    
    # 根据你指定的路径规则分类存放
    folder = "eyoucms/core" if pkg_type == "core" else "eyoucms/muban"
    # 生成唯一文件名防止覆盖
    safe_filename = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    obs_key = f"{folder}/{safe_filename}"
    
    # 读取文件并上传到 OBS
    file_bytes = await file.read()
    success = obs_client.upload_file_bytes(obs_key, file_bytes)
    
    if not success:
        raise HTTPException(status_code=500, detail="上传到华为云 OBS 失败")
    
    # 存入数据库
    new_template = TemplatePackage(name=name, pkg_type=pkg_type, obs_path=obs_key)
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template

@router.get("/", response_model=List[TemplateResponse])
def get_templates(pkg_type: str = None, db: Session = Depends(get_db)):
    query = db.query(TemplatePackage)
    if pkg_type:
        query = query.filter(TemplatePackage.pkg_type == pkg_type)
    return query.order_by(TemplatePackage.id.desc()).all()

@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    tpl = db.query(TemplatePackage).filter(TemplatePackage.id == template_id).first()
    if not tpl: raise HTTPException(status_code=404)
    # 生产环境中最好也同步删除 OBS 里的实体文件，这里简单处理先删库
    db.delete(tpl)
    db.commit()
    return {"status": "success"}
