from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid
import os

from core.database import get_db
from core.runtime_paths import TMP_UPLOAD_DIR, LANDING_PAGES_DIR
from models.asset import LandingPagePackage
from schemas.asset import LandingPageResponse
from services.audit_service import log_operation, create_task_log, update_task_log
from worker.deploy_tasks import process_landing_upload

router = APIRouter()


@router.post("/upload")
async def upload_landing(
    file: UploadFile = File(...),
    name: str = Form(...),
    remark: str = Form(""),
    landing_page_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="只允许上传 .zip")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")

    tmp_path = str(TMP_UPLOAD_DIR / f"{uuid.uuid4().hex}_{file.filename}")
    with open(tmp_path, "wb") as fp:
        fp.write(file_bytes)

    is_cover = bool(landing_page_id)
    task_log = create_task_log(
        db,
        task_type="landing_upload",
        task_name="落地页上传",
        message=f"已入队：{'覆盖上传' if is_cover else '上传'} {name}",
        detail={"name": name, "remark": remark, "filename": file.filename, "landing_page_id": landing_page_id},
        status="queued",
    )
    task = process_landing_upload.delay(task_log.id, tmp_path, name, remark, None, landing_page_id, file.filename)
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="landing.upload.submit",
        message=f"提交落地页{'覆盖' if is_cover else ''}上传：{name}",
        detail={"task_log_id": task_log.id, "task_id": task.id, "name": name, "landing_page_id": landing_page_id},
    )
    return {"status": "success", "queued": 1, "task_log_id": task_log.id, "task_id": task.id}


@router.get("/", response_model=List[LandingPageResponse])
def get_landings(db: Session = Depends(get_db)):
    return db.query(LandingPagePackage).order_by(LandingPagePackage.id.desc()).all()


@router.put("/{landing_id}", response_model=LandingPageResponse)
def update_landing_meta(
    landing_id: int,
    name: Optional[str] = None,
    remark: Optional[str] = None,
    db: Session = Depends(get_db),
):
    item = db.query(LandingPagePackage).filter(LandingPagePackage.id == landing_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="落地页不存在")
    if name is not None:
        cleaned = str(name).strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="名称不能为空")
        item.name = cleaned
    if remark is not None:
        item.remark = str(remark)
    db.commit()
    db.refresh(item)
    log_operation(db, action="landing.update_meta", message=f"更新落地页信息: {item.name}", detail={"landing_page_id": landing_id})
    return item


@router.delete("/{landing_id}")
def delete_landing(landing_id: int, db: Session = Depends(get_db)):
    item = db.query(LandingPagePackage).filter(LandingPagePackage.id == landing_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="落地页不存在")
    name = item.name
    db.delete(item)
    db.commit()
    preview_dir = str(LANDING_PAGES_DIR / str(landing_id))
    try:
        if os.path.isdir(preview_dir):
            for entry in os.listdir(preview_dir):
                full = os.path.join(preview_dir, entry)
                if os.path.isdir(full):
                    import shutil
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    try:
                        os.remove(full)
                    except Exception:
                        pass
    except Exception:
        pass
    log_operation(db, action="landing.delete", message=f"删除落地页: {name}", detail={"landing_page_id": landing_id})
    return {"status": "success"}
