from __future__ import annotations

from dataclasses import dataclass

from .accounts import AccountConfig
from .config import Settings
from .db import Database
from .llm import LLM
from .pipeline import Pipeline
from .providers import build_providers
from .publish_service import PublishService
from .publisher import build_publisher


@dataclass
class AccountRuntime:
    account: AccountConfig
    pipeline: Pipeline
    publish_service: PublishService


def build_runtimes(accounts: list[AccountConfig], settings: Settings, db: Database,
                   llm: LLM | None = None) -> list[AccountRuntime]:
    active = [account for account in accounts if account.enabled]
    ids = db.sync_accounts(active)
    for account in active:
        account.id = ids.get(account.slug, account.id)

    shared_llm = llm or LLM(settings)
    runtimes: list[AccountRuntime] = []
    for account in active:
        publisher = build_publisher(settings, account)
        publish_service = PublishService(settings, db, publisher, account)
        pipeline = Pipeline(
            settings, db, llm=shared_llm,
            providers=build_providers(account, settings), account=account,
        )
        runtimes.append(AccountRuntime(
            account=account, pipeline=pipeline, publish_service=publish_service,
        ))
    return runtimes