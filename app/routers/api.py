from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.query_service import read_distinct_values, read_fleet_history_by_plate, read_job_orders

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/jobs")
def api_jobs(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    return {"items": read_job_orders(db, limit=limit)}


@router.get("/fleet-history")
def api_fleet_history(plate: str, limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    return {"items": read_fleet_history_by_plate(db, plate_number=plate, limit=limit)}


@router.get("/distinct-values")
def api_distinct_values(value_type: str, db: Session = Depends(get_db)):
    return {"items": read_distinct_values(db, value_type=value_type)}

