import json
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from models.audit_log import OperationLog, TaskLog


def _to_json_text(data: Any) -> Optional[str]:
    if data is None:
        return None
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return str(data)


def log_operation(
    db: Session,
    action: str,
    message: str,
    detail: Any = None,
    username: Optional[str] = None,
) -> OperationLog:
    item = OperationLog(
        username=(username or None),
        action=str(action or "").strip() or "unknown",
        message=str(message or "").strip() or "-",
        detail=_to_json_text(detail),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def create_task_log(
    db: Session,
    task_type: str,
    task_name: str,
    message: str = "",
    detail: Any = None,
    username: Optional[str] = None,
    status: str = "queued",
) -> TaskLog:
    item = TaskLog(
        task_type=str(task_type or "").strip() or "unknown",
        task_name=str(task_name or "").strip() or "未命名任务",
        status=status or "queued",
        username=(username or None),
        message=(message or None),
        detail=_to_json_text(detail),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_task_log(
    db: Session,
    task_log_id: int,
    status: Optional[str] = None,
    message: Optional[str] = None,
    detail: Any = None,
    task_ref: Optional[str] = None,
):
    task = db.query(TaskLog).filter(TaskLog.id == task_log_id).first()
    if not task:
        return None
    if status is not None:
        task.status = status
        if status in {"success", "failed"}:
            task.finished_at = datetime.now(timezone.utc)
    if message is not None:
        task.message = message
    if detail is not None:
        task.detail = _to_json_text(detail)
    if task_ref is not None:
        task.task_ref = task_ref
    db.commit()
    db.refresh(task)
    return task
