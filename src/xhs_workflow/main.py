from __future__ import annotations

import logging

from .accounts import load_accounts
from .app import create_app
from .config import settings
from .db import Database
from .runtime import build_runtimes

logging.basicConfig(level=logging.INFO)

db = Database(settings.database_path)
accounts = load_accounts(settings)
runtimes = build_runtimes(accounts, settings, db)
app = create_app(settings, db, runtimes)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("xhs_workflow.main:app", host=settings.app_host, port=settings.app_port, reload=False)