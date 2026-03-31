from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.database import get_db
from models.audit_log import OperationLog, TaskLog
from schemas.audit import OperationLogResponse, TaskLogResponse

router = APIRouter()


@router.get("/operations", response_model=list[OperationLogResponse])
def get_operation_logs(limit: int = 100, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 500))
    return db.query(OperationLog).order_by(OperationLog.id.desc()).limit(limit).all()


@router.get("/tasks", response_model=list[TaskLogResponse])
def get_task_logs(limit: int = 100, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 500))
    return db.query(TaskLog).order_by(TaskLog.id.desc()).limit(limit).all()


@router.get("/tasks/{task_log_id}", response_model=TaskLogResponse)
def get_task_log(task_log_id: int, db: Session = Depends(get_db)):
    item = db.query(TaskLog).filter(TaskLog.id == task_log_id).first()
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="任务日志不存在")
    return item
