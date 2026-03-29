from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from core.database import get_db
from models.asset import TDKConfig
from schemas.asset import TDKCreate, TDKResponse

router = APIRouter()

@router.post("/", response_model=TDKResponse)
def create_tdk(tdk: TDKCreate, db: Session = Depends(get_db)):
    new_tdk = TDKConfig(**tdk.model_dump())
    db.add(new_tdk)
    db.commit()
    db.refresh(new_tdk)
    return new_tdk

# 💡 新增：批量插入 TDK 接口
@router.post("/batch", response_model=List[TDKResponse])
def create_tdks_batch(tdks: List[TDKCreate], db: Session = Depends(get_db)):
    new_tdks = [TDKConfig(**tdk.model_dump()) for tdk in tdks]
    db.add_all(new_tdks)
    db.commit()
    for t in new_tdks:
        db.refresh(t)
    return new_tdks

@router.get("/", response_model=List[TDKResponse])
def get_tdks(db: Session = Depends(get_db)):
    return db.query(TDKConfig).order_by(TDKConfig.id.desc()).all()

@router.delete("/{tdk_id}")
def delete_tdk(tdk_id: int, db: Session = Depends(get_db)):
    tdk = db.query(TDKConfig).filter(TDKConfig.id == tdk_id).first()
    if not tdk: raise HTTPException(status_code=404, detail="未找到")
    db.delete(tdk)
    db.commit()
    return {"status": "success"}
