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
