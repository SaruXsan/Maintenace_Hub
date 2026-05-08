from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import BootstrapConfig, load_bootstrap


SessionLocal = sessionmaker(autocommit=False, autoflush=False)


def build_sqlserver_url(cfg: BootstrapConfig) -> str:
    return (
        "mssql+pyodbc://"
        f"{cfg.db_user}:{cfg.db_password}@{cfg.db_host}/{cfg.db_name}"
        f"?driver={cfg.db_driver.replace(' ', '+')}&TrustServerCertificate=yes"
    )


def get_engine() -> Engine | None:
    cfg = load_bootstrap()
    if not cfg.has_db:
        return None
    return create_engine(build_sqlserver_url(cfg), pool_pre_ping=True)


def get_db() -> Session:
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Database is not configured")
    SessionLocal.configure(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

