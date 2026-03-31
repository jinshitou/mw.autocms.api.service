from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List
import uuid
import os

from core.database import get_db
from models.asset import TemplatePackage
from schemas.asset import TemplateResponse
from services.audit_service import log_operation, create_task_log, update_task_log
from worker.deploy_tasks import process_template_upload

router = APIRouter()

@router.post("/upload")
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
    
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")
    tmp_dir = "/app/tmp_uploads"
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_{file.filename}")
    with open(tmp_path, "wb") as fp:
        fp.write(file_bytes)

    task_log = create_task_log(
        db,
        task_type="template_upload",
        task_name="资源包上传",
        message=f"已入队：{name} ({pkg_type})",
        detail={"name": name, "pkg_type": pkg_type, "filename": file.filename},
        status="queued",
    )
    task = process_template_upload.delay(task_log.id, tmp_path, pkg_type, name, file.filename)
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="template.upload.submit",
        message=f"提交资源包上传：{name} ({pkg_type})",
        detail={"task_log_id": task_log.id, "task_id": task.id, "name": name, "pkg_type": pkg_type},
    )
    return {"status": "success", "queued": 1, "task_log_id": task_log.id, "task_id": task.id}

@router.get("/", response_model=List[TemplateResponse])
def get_templates(pkg_type: str = None, db: Session = Depends(get_db)):
    query = db.query(TemplatePackage)
    if pkg_type: query = query.filter(TemplatePackage.pkg_type == pkg_type)
    return query.order_by(TemplatePackage.id.desc()).all()

@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    tpl = db.query(TemplatePackage).filter(TemplatePackage.id == template_id).first()
    if not tpl: raise HTTPException(status_code=404)
    tpl_name = tpl.name
    tpl_type = tpl.pkg_type
    db.delete(tpl)
    db.commit()
    log_operation(db, action="template.delete", message=f"删除资源包: {tpl_name} ({tpl_type})", detail={"template_id": template_id})
    return {"status": "success"}
