from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import BootstrapConfig, save_bootstrap
from app.database import build_sqlserver_url
from app.models import Base


def save_connection_settings(cfg: BootstrapConfig) -> None:
    save_bootstrap(cfg)


def test_sql_connection(cfg: BootstrapConfig) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(build_sqlserver_url(cfg), pool_pre_ping=True)
    with Session(engine) as db:
        db.execute(text("SELECT 1"))


def initialize_portal_objects(cfg: BootstrapConfig) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(build_sqlserver_url(cfg), pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        setting_pairs = {
            "ad_server": cfg.ad_server,
            "ad_domain": cfg.ad_domain,
            "telegram_bot_token": cfg.telegram_bot_token,
            "ai_api_url": cfg.ai_api_url,
            "ai_api_key": cfg.ai_api_key,
            "ai_model": cfg.ai_model,
            "dev_mode": str(cfg.dev_mode),
            "dev_admin_user": cfg.dev_admin_user,
            "dev_admin_password": cfg.dev_admin_password,
            "updated_at": datetime.utcnow().isoformat(),
        }
        for key, value in setting_pairs.items():
            db.execute(
                text(
                    """
                    MERGE app_settings AS target
                    USING (SELECT :key AS [key], :value AS [value]) AS source
                    ON target.[key] = source.[key]
                    WHEN MATCHED THEN UPDATE SET target.[value] = source.[value], target.updated_at = GETUTCDATE()
                    WHEN NOT MATCHED THEN INSERT ([key], [value], updated_at) VALUES (source.[key], source.[value], GETUTCDATE());
                    """
                ),
                {"key": key, "value": value},
            )
        db.commit()

        # Read-only interfaces to business tables provided by the user.
        db.execute(
            text(
                """
                IF OBJECT_ID('dbo.eet_distinct_values', 'U') IS NOT NULL
                   AND OBJECT_ID('dbo.vw_distinct_values', 'V') IS NULL
                EXEC('
                    CREATE VIEW dbo.vw_distinct_values AS
                    SELECT [value], [type], trxstatus
                    FROM dbo.eet_distinct_values
                ')
                """
            )
        )
        db.execute(
            text(
                """
                IF OBJECT_ID('dbo.fleet_history_files', 'U') IS NOT NULL
                   AND OBJECT_ID('dbo.vw_fleet_history_files', 'V') IS NULL
                EXEC('
                    CREATE VIEW dbo.vw_fleet_history_files AS
                    SELECT *
                    FROM dbo.fleet_history_files
                ')
                """
            )
        )
        db.execute(
            text(
                """
                IF OBJECT_ID('dbo.daoud_job_order', 'U') IS NOT NULL
                   AND OBJECT_ID('dbo.vw_jobs', 'V') IS NULL
                EXEC('
                    CREATE VIEW dbo.vw_jobs AS
                    SELECT *
                    FROM dbo.daoud_job_order
                ')
                """
            )
        )
        db.commit()


def initialize_database(cfg: BootstrapConfig) -> None:
    from sqlalchemy import create_engine

    master_url = (
        "mssql+pyodbc://"
        f"{cfg.db_user}:{cfg.db_password}@{cfg.db_host}/master"
        f"?driver={cfg.db_driver.replace(' ', '+')}&TrustServerCertificate=yes"
    )
    master_engine = create_engine(master_url, pool_pre_ping=True)
    with Session(master_engine) as db:
        db.execute(
            text(
                """
                IF DB_ID(:db_name) IS NULL
                BEGIN
                    DECLARE @sql NVARCHAR(MAX) = N'CREATE DATABASE [' + :db_name + N']';
                    EXEC (@sql);
                END
                """
            ),
            {"db_name": cfg.db_name},
        )
        db.commit()

    initialize_portal_objects(cfg)

