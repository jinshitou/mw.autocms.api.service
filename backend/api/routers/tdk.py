from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from core.database import get_db
from models.asset import TDKConfig
from schemas.asset import TDKCreate, TDKResponse
from services.audit_service import log_operation, create_task_log, update_task_log
from worker.deploy_tasks import process_tdk_batch_import

router = APIRouter()

@router.post("/", response_model=TDKResponse)
def create_tdk(tdk: TDKCreate, db: Session = Depends(get_db)):
    new_tdk = TDKConfig(**tdk.model_dump())
    db.add(new_tdk)
    db.commit()
    db.refresh(new_tdk)
    log_operation(db, action="tdk.create", message=f"新增TDK: {new_tdk.name}", detail={"tdk_id": new_tdk.id})
    return new_tdk

# 💡 新增：批量插入 TDK 接口
@router.post("/batch")
def create_tdks_batch(tdks: List[TDKCreate], db: Session = Depends(get_db)):
    if not tdks:
        raise HTTPException(status_code=400, detail="TDK 列表不能为空")
    payload = [tdk.model_dump() for tdk in tdks]
    task_log = create_task_log(
        db,
        task_type="tdk_batch_import",
        task_name="批量导入TDK",
        message=f"已入队：{len(payload)} 条",
        detail={"count": len(payload)},
        status="queued",
    )
    task = process_tdk_batch_import.delay(task_log.id, payload)
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="tdk.batch_create.submit",
        message=f"提交批量导入TDK：{len(payload)} 条",
        detail={"count": len(payload), "task_log_id": task_log.id, "task_id": task.id},
    )
    return {"status": "success", "queued": len(payload), "task_log_id": task_log.id, "task_id": task.id}

@router.get("/", response_model=List[TDKResponse])
def get_tdks(db: Session = Depends(get_db)):
    return db.query(TDKConfig).order_by(TDKConfig.id.desc()).all()

@router.delete("/{tdk_id}")
def delete_tdk(tdk_id: int, db: Session = Depends(get_db)):
    tdk = db.query(TDKConfig).filter(TDKConfig.id == tdk_id).first()
    if not tdk: raise HTTPException(status_code=404, detail="未找到")
    tdk_name = tdk.name
    db.delete(tdk)
    db.commit()
    log_operation(db, action="tdk.delete", message=f"删除TDK: {tdk_name}", detail={"tdk_id": tdk_id})
    return {"status": "success"}
