from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BOOTSTRAP_FILE = Path(".bootstrap.json")


@dataclass
class BootstrapConfig:
    db_host: str = ""
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    db_driver: str = "ODBC Driver 17 for SQL Server"
    ad_server: str = ""
    ad_domain: str = ""
    telegram_bot_token: str = ""
    ai_api_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    dev_admin_user: str = "admin"
    dev_admin_password: str = "admin123"
    dev_mode: bool = True

    @property
    def has_db(self) -> bool:
        return bool(self.db_host and self.db_name and self.db_user)


def load_bootstrap() -> BootstrapConfig:
    if not BOOTSTRAP_FILE.exists():
        return BootstrapConfig()
    data: dict[str, Any] = json.loads(BOOTSTRAP_FILE.read_text(encoding="utf-8"))
    return BootstrapConfig(**data)


def save_bootstrap(config: BootstrapConfig) -> None:
    BOOTSTRAP_FILE.write_text(
        json.dumps(config.__dict__, indent=2),
        encoding="utf-8",
    )

