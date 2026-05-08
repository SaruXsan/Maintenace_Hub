from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def read_fleet_history_by_plate(db: Session, plate_number: str, limit: int = 10) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT TOP (:limit)
                [Purchase Date], [Invoice Number], [Item Description], Qty, [Amount LBP], [Amount USD], [Plate Number], TrxStatus
            FROM dbo.vw_fleet_history_files
            WHERE [Plate Number] = :plate
            ORDER BY [Purchase Date] DESC
            """
        ),
        {"plate": plate_number, "limit": limit},
    ).mappings()
    return [dict(r) for r in rows]


def read_job_orders(db: Session, limit: int = 10) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT TOP (:limit) JobID, JobDate, JobCustName, JobTech, JobEquipment, JobAttachName
            FROM dbo.vw_jobs
            ORDER BY JobDate DESC
            """
        ),
        {"limit": limit},
    ).mappings()
    return [dict(r) for r in rows]


def read_distinct_values(db: Session, value_type: str) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT [value], [type], trxstatus
            FROM dbo.vw_distinct_values
            WHERE [type] = :value_type
            ORDER BY [value]
            """
        ),
        {"value_type": value_type},
    ).mappings()
    return [dict(r) for r in rows]

