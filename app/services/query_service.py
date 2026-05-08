from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def _resolve_distinct_values_source(db: Session) -> str:
    candidates = (
        ("dbo.vw_distinct_values", "V"),
        ("dbo.daoud_fleet_distinct_values", "U"),
        ("dbo.eet_distinct_values", "U"),
        ("dbo.distinct_values", "U"),
    )
    for object_name, object_type in candidates:
        object_exists = db.scalar(
            text("SELECT OBJECT_ID(:name, :obj_type)"),
            {"name": object_name, "obj_type": object_type},
        )
        if object_exists is not None:
            return object_name
    raise RuntimeError(
        "Distinct values source not found. Expected one of: "
        "dbo.vw_distinct_values, dbo.daoud_fleet_distinct_values, dbo.eet_distinct_values, dbo.distinct_values."
    )


def _resolve_fleet_history_source(db: Session) -> str:
    candidates = (
        ("dbo.vw_fleet_history_files", "V"),
        ("dbo.daoud_fleet_history_files", "U"),
        ("dbo.fleet_history_files", "U"),
    )
    for object_name, object_type in candidates:
        object_exists = db.scalar(
            text("SELECT OBJECT_ID(:name, :obj_type)"),
            {"name": object_name, "obj_type": object_type},
        )
        if object_exists is not None:
            return object_name
    raise RuntimeError(
        "Fleet history source not found. Expected one of: "
        "dbo.vw_fleet_history_files, dbo.daoud_fleet_history_files, dbo.fleet_history_files."
    )


def read_fleet_history_by_plate(db: Session, plate_number: str, limit: int = 10) -> list[dict]:
    source = _resolve_fleet_history_source(db)
    rows = db.execute(
        text(
            f"""
            SELECT TOP (:limit)
                [Purchase Date], [Invoice Number], [Item Description], Qty, [Amount LBP], [Amount USD], [Plate Number], TrxStatus
            FROM {source}
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


def read_job_orders_filtered(
    db: Session,
    search: str = "",
    customer: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
) -> list[dict]:
    clauses: list[str] = []
    params: dict[str, object] = {"limit": limit}

    if search:
        clauses.append(
            "(CAST(JobID AS NVARCHAR(50)) LIKE :search OR JobCustName LIKE :search OR JobTech LIKE :search OR JobEquipment LIKE :search)"
        )
        params["search"] = f"%{search}%"
    if customer:
        clauses.append("JobCustName LIKE :customer")
        params["customer"] = f"%{customer}%"
    if date_from:
        clauses.append("JobDate >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("JobDate <= :date_to")
        params["date_to"] = date_to

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        text(
            f"""
            SELECT TOP (:limit) JobID, JobDate, JobCustName, JobTech, JobEquipment, JobAttachName
            FROM dbo.vw_jobs
            {where_clause}
            ORDER BY JobDate DESC
            """
        ),
        params,
    ).mappings()
    return [dict(r) for r in rows]


def read_distinct_values(db: Session, value_type: str) -> list[dict]:
    source = _resolve_distinct_values_source(db)
    rows = db.execute(
        text(
            f"""
            SELECT [value], [type], trxstatus
            FROM {source}
            WHERE [type] = :value_type
            ORDER BY [value]
            """
        ),
        {"value_type": value_type},
    ).mappings()
    return [dict(r) for r in rows]


def read_distinct_values_filtered(
    db: Session,
    value_type: str = "",
    value_search: str = "",
    trxstatus: str = "",
    limit: int = 200,
) -> list[dict]:
    source = _resolve_distinct_values_source(db)
    clauses: list[str] = []
    params: dict[str, object] = {"limit": limit}

    if value_type:
        clauses.append("[type] = :value_type")
        params["value_type"] = value_type
    if value_search:
        clauses.append("[value] LIKE :value_search")
        params["value_search"] = f"%{value_search}%"
    if trxstatus:
        clauses.append("trxstatus = :trxstatus")
        params["trxstatus"] = trxstatus

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        text(
            f"""
            SELECT TOP (:limit) [value], [type], trxstatus
            FROM {source}
            {where_clause}
            ORDER BY [type], [value]
            """
        ),
        params,
    ).mappings()
    return [dict(r) for r in rows]


def list_distinct_types(db: Session) -> list[str]:
    source = _resolve_distinct_values_source(db)
    rows = db.execute(
        text(
            f"""
            SELECT DISTINCT [type]
            FROM {source}
            WHERE [type] IS NOT NULL AND [type] <> ''
            ORDER BY [type]
            """
        )
    ).scalars()
    return [str(v) for v in rows]


def read_fleet_history_filtered(
    db: Session,
    plate_number: str = "",
    trxstatus: str = "",
    item_search: str = "",
    invoice_number: str = "",
    limit: int = 100,
) -> list[dict]:
    source = _resolve_fleet_history_source(db)
    clauses: list[str] = []
    params: dict[str, object] = {"limit": limit}

    if plate_number:
        clauses.append("[Plate Number] LIKE :plate_number")
        params["plate_number"] = f"%{plate_number}%"
    if trxstatus:
        clauses.append("TrxStatus = :trxstatus")
        params["trxstatus"] = trxstatus
    if item_search:
        clauses.append("[Item Description] LIKE :item_search")
        params["item_search"] = f"%{item_search}%"
    if invoice_number:
        clauses.append("[Invoice Number] LIKE :invoice_number")
        params["invoice_number"] = f"%{invoice_number}%"

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        text(
            f"""
            SELECT TOP (:limit)
                [Purchase Date], [Invoice Number], [Item Description], Qty, [Amount LBP], [Amount USD], [Plate Number], TrxStatus
            FROM {source}
            {where_clause}
            ORDER BY [Purchase Date] DESC
            """
        ),
        params,
    ).mappings()
    return [dict(r) for r in rows]

