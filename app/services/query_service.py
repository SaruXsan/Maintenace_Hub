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


_SUPPLIER_NAME_CANDIDATES: tuple[str, ...] = (
    "Source",
    "Supplier Name",
    "Supplier",
    "SupplierName",
    "Vendor Name",
    "Vendor",
    "CustName",
    "Customer Name",
    "Party Name",
)


def _fleet_supplier_fragment(column_name: str) -> str:
    safe = column_name.replace("]", "]]")
    return f", [{safe}] AS supplier_name"


def _fleet_row_columns_compile_probe(
    db: Session,
    source: str,
    cols_from_table: str,
    cols_from_cte: str,
    order_by: str,
) -> bool:
    """True if SQL Server accepts the paginated fleet CTE (inner list vs outer list differ when aliasing)."""
    try:
        db.execute(
            text(
                f"""
                WITH ordered AS (
                    SELECT
                        {cols_from_table},
                        ROW_NUMBER() OVER (ORDER BY [Purchase Date] {order_by}) AS rn
                    FROM {source}
                    WHERE 0 = 1
                )
                SELECT {cols_from_cte}
                FROM ordered
                WHERE rn > 0 AND rn <= 1
                """
            )
        )
        return True
    except Exception:
        db.rollback()
        return False


def _extra_supplier_like_column_names(db: Session, source: str) -> list[str]:
    """Names on this fleet object that look supplier/vendor-related (not in the fixed candidate list)."""
    try:
        full_name = source.replace("[", "").replace("]", "")
        oid = db.scalar(text("SELECT OBJECT_ID(:n)"), {"n": full_name})
        if oid is None:
            return []
        rows = db.execute(
            text("SELECT name FROM sys.columns WHERE object_id = :oid"),
            {"oid": int(oid)},
        ).fetchall()
    except Exception:
        return []

    fixed_lower = {c.lower() for c in _SUPPLIER_NAME_CANDIDATES}
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        actual = str(r[0])
        low = actual.lower()
        if low in fixed_lower or low in seen:
            continue
        if "supplier" in low or "vendor" in low:
            seen.add(low)
            out.append(actual)
    return out


def _fleet_row_projection(db: Session, source: str, order_by: str = "DESC") -> tuple[str, str]:
    """Return (columns_from_base_table, columns_from_ordered_cte).

    Inner CTE selects physical columns and may use `[Source] AS supplier_name`. The CTE output
    column is then `supplier_name`, so the outer SELECT must list `[supplier_name]`, not `[Source]`
    again — reusing the inner fragment in the outer clause breaks pagination and hid supplier in Telegram.
    """
    base = (
        "[Purchase Date], [Invoice Number], [Item Description], Qty, "
        "[Amount LBP], [Amount USD], [Plate Number], TrxStatus"
    )
    ob = "DESC" if order_by.upper() != "ASC" else "ASC"
    outer_with_supplier = base + ", [supplier_name]"
    for name in _SUPPLIER_NAME_CANDIDATES:
        inner = base + _fleet_supplier_fragment(name)
        if _fleet_row_columns_compile_probe(db, source, inner, outer_with_supplier, ob):
            return (inner, outer_with_supplier)
    for name in _extra_supplier_like_column_names(db, source):
        inner = base + _fleet_supplier_fragment(name)
        if _fleet_row_columns_compile_probe(db, source, inner, outer_with_supplier, ob):
            return (inner, outer_with_supplier)
    return (base, base)


def _resolve_fleet_history_source(db: Session) -> str:
    """Pick the first existing fleet table/view.

    Prefer `dbo.daoud_fleet_history_files` when present (main operational table in this project), then
    the generic view/table names used by older installs.
    """
    candidates = (
        ("dbo.daoud_fleet_history_files", "U"),
        ("dbo.vw_fleet_history_files", "V"),
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
        "dbo.daoud_fleet_history_files, dbo.vw_fleet_history_files, dbo.fleet_history_files."
    )


def read_fleet_history_by_plate(
    db: Session, plate_number: str, limit: int = 10, newest_first: bool = True
) -> list[dict]:
    source = _resolve_fleet_history_source(db)
    order_by = "DESC" if newest_first else "ASC"
    cols, _ = _fleet_row_projection(db, source, order_by)
    rows = db.execute(
        text(
            f"""
            SELECT TOP (:limit)
                {cols}
            FROM {source}
            WHERE [Plate Number] = :plate
            ORDER BY [Purchase Date] {order_by}
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
    newest_first: bool = True,
) -> list[dict]:
    source = _resolve_fleet_history_source(db)
    clauses: list[str] = []
    params: dict[str, object] = {"limit": limit}
    order_by = "DESC" if newest_first else "ASC"

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
    cols, _ = _fleet_row_projection(db, source, order_by)
    rows = db.execute(
        text(
            f"""
            SELECT TOP (:limit)
                {cols}
            FROM {source}
            {where_clause}
            ORDER BY [Purchase Date] {order_by}
            """
        ),
        params,
    ).mappings()
    return [dict(r) for r in rows]


def count_fleet_history_filtered(
    db: Session,
    plate_number: str = "",
    trxstatus: str = "",
    item_search: str = "",
    invoice_number: str = "",
) -> int:
    source = _resolve_fleet_history_source(db)
    clauses: list[str] = []
    params: dict[str, object] = {}

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
    value = db.scalar(
        text(
            f"""
            SELECT COUNT(1)
            FROM {source}
            {where_clause}
            """
        ),
        params,
    )
    return int(value or 0)


def read_fleet_history_filtered_page(
    db: Session,
    plate_number: str = "",
    trxstatus: str = "",
    item_search: str = "",
    invoice_number: str = "",
    offset: int = 0,
    page_size: int = 10,
    newest_first: bool = True,
) -> list[dict]:
    source = _resolve_fleet_history_source(db)
    clauses: list[str] = []
    params: dict[str, object] = {"offset": max(offset, 0), "page_size": max(page_size, 1)}
    order_by = "DESC" if newest_first else "ASC"

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
    cols_inner, cols_outer = _fleet_row_projection(db, source, order_by)
    rows = db.execute(
        text(
            f"""
            WITH ordered AS (
                SELECT
                    {cols_inner},
                    ROW_NUMBER() OVER (ORDER BY [Purchase Date] {order_by}) AS rn
                FROM {source}
                {where_clause}
            )
            SELECT
                {cols_outer}
            FROM ordered
            WHERE rn > :offset AND rn <= (:offset + :page_size)
            ORDER BY rn
            """
        ),
        params,
    ).mappings()
    return [dict(r) for r in rows]

