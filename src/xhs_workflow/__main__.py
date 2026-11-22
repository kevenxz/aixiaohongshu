from __future__ import annotations

import argparse
import json
import re
import sys

from .accounts import AccountConfig, load_accounts


def _cjk_count(text: str) -> int:
    return len(re.findall(r"[\u3400-\u4dbf\u9fff]", text or ""))


def _needs_translation(post: dict) -> bool:
    return _cjk_count(post["title"]) == 0 or _cjk_count(post["body"]) < 30


def _selected_accounts(selector: str) -> list[AccountConfig]:
    from .config import settings
    from .db import Database

    accounts = load_accounts(settings)
    if selector and selector != "all":
        accounts = [account for account in accounts if account.slug == selector]
        if not accounts:
            print(json.dumps({"ok": False, "error": f"unknown account: {selector}"},
                             ensure_ascii=False, indent=2))
            return []
    elif not selector:
        first = next((account for account in accounts if account.enabled), None)
        accounts = [first] if first else []
    accounts = [account for account in accounts if account.enabled]
    db = Database(settings.database_path)
    ids = db.sync_accounts(accounts)
    for account in accounts:
        account.id = ids.get(account.slug, account.id)
    return accounts


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-platform content workflow")
    parser.add_argument(
        "command",
        choices=("run-once", "check-sources", "serve", "retranslate"),
        nargs="?",
        default="serve",
    )
    parser.add_argument("--dry-run", action="store_true", help="list posts without calling the LLM")
    parser.add_argument("--limit", type=int, default=0, help="max posts to retranslate (0 = all)")
    parser.add_argument(
        "--platform",
        choices=("all", "reddit", "youtube", "x", "web", "weibo", "instagram"),
        default="all",
        help="only retranslate one source platform",
    )
    parser.add_argument(
        "--account", default="", help="account slug, or 'all' to process every enabled account"
    )
    args = parser.parse_args()

    if args.command == "run-once":
        from .config import settings
        from .pipeline import Pipeline

        accounts = _selected_accounts(args.account)
        results = []
        for account in accounts:
            results.append(Pipeline(settings, Database(settings.database_path), account=account).run_once())
        print(json.dumps(results if len(results) != 1 else results[0], ensure_ascii=False, indent=2))
        return

    if args.command == "check-sources":
        from .config import settings
        from .providers import build_providers

        accounts = _selected_accounts(args.account)
        output = []
        for account in accounts:
            results = []
            for provider in build_providers(account, settings):
                try:
                    items = provider.fetch(3)
                    sample = next((item.title or item.text for item in items if item.title or item.text), "")
                    results.append({
                        "provider": provider.name,
                        "ok": True,
                        "count": len(items),
                        "sample": sample[:80].replace("\n", " "),
                    })
                except Exception as exc:
                    results.append({"provider": provider.name, "ok": False, "error": str(exc)})
            output.append({"account": account.slug, "providers": results})
        print(json.dumps({
            "accounts": output if len(output) != 1 else output[0],
            "llm_configured": bool(settings.llm_api_key and settings.llm_model),
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "retranslate":
        from .config import settings
        from .db import Database
        from .llm import LLM
        from .models import SourceItem

        if not args.dry_run and not (settings.llm_api_key and settings.llm_model):
            print(json.dumps({"ok": False, "error": "LLM_API_KEY / LLM_MODEL 未配置"},
                             ensure_ascii=False, indent=2))
            return

        accounts = _selected_accounts(args.account)
        account_by_id = {account.id: account for account in accounts}
        if not accounts:
            return
        db = Database(settings.database_path)
        where = "p.status IN ('draft', 'approved')"
        params: list[object] = []
        if args.platform != "all":
            where += " AND s.platform=?"
            params.append(args.platform)
        if args.account and args.account != "all":
            where += " AND p.account_id=?"
            params.append(accounts[0].id)
        with db.connect() as conn:
            rows = conn.execute(
                f"""SELECT p.id, p.account_id, p.source_item_id, p.title, p.body, p.status,
                           p.risk_flags_json,
                           s.platform, s.source_id, s.source_url, s.author, s.title AS source_title,
                           s.text, s.published_at, s.media_url, s.media_type, s.rights_status,
                           s.heat_score, s.heat_label, s.ai_score, s.raw_json
                    FROM posts p JOIN source_items s ON s.id = p.source_item_id
                    WHERE {where}
                    ORDER BY p.id""",
                params,
            ).fetchall()

        candidates = [dict(row) for row in rows if _needs_translation(dict(row))]
        if args.limit > 0:
            candidates = candidates[:args.limit]

        summary = {
            "ok": True,
            "dry_run": args.dry_run,
            "account": args.account or "all",
            "platform": args.platform,
            "checked": len(rows),
            "untranslated": len(candidates),
            "updated": 0,
            "failed": 0,
            "posts": [
                {"id": post["id"], "platform": post["platform"], "status": post["status"],
                 "title": post["title"][:60]}
                for post in candidates
            ],
            "errors": [],
        }
        if args.dry_run:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        llm = LLM(settings)
        for index, post in enumerate(candidates, start=1):
            print(f"[{index}/{len(candidates)}] post {post['id']}: {post['title'][:50]}",
                  file=sys.stderr, flush=True)
            source = db.get_source_item(post["source_item_id"])
            if not source:
                summary["failed"] += 1
                summary["errors"].append(f"post {post['id']}: source item missing")
                continue
            try:
                metadata = json.loads(source.get("raw_json") or "{}")
            except json.JSONDecodeError:
                metadata = {}
            item = SourceItem(
                platform=source["platform"],
                source_id=source["source_id"],
                source_url=source["source_url"],
                author=source["author"] or "",
                title=source["title"] or "",
                text=source["text"] or "",
                published_at=source["published_at"],
                media_url=source["media_url"],
                media_type=source["media_type"],
                media_urls=metadata.get("media_urls") or metadata.get("images") or [],
                rights_status=source["rights_status"] or "unknown",
                heat_score=float(source["heat_score"] or 0),
                heat_label=source["heat_label"] or "",
                ai_score=float(source["ai_score"] or 0),
                metadata=metadata,
            )
            account = account_by_id.get(int(post["account_id"]))
            try:
                draft = llm.transform(item, account)
                if _cjk_count(draft.title) == 0 or _cjk_count(draft.body) < 80:
                    raise RuntimeError("模型输出仍缺少中文")
                flags = [str(flag) for flag in json.loads(post["risk_flags_json"] or "[]")
                         if not str(flag).startswith("LLM未完成")]
                for flag in draft.risk_flags:
                    if flag not in flags:
                        flags.append(flag)
                if not db.update_post(post["id"], title=draft.title, body=draft.body,
                                      tags=draft.tags, risk_flags=flags):
                    raise RuntimeError("post update failed")
                summary["updated"] += 1
                print(f"    -> {draft.title}", file=sys.stderr, flush=True)
            except Exception as exc:
                summary["failed"] += 1
                summary["ok"] = False
                summary["errors"].append(f"post {post['id']}: {exc}")
                print(f"    !! {exc}", file=sys.stderr, flush=True)

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    import uvicorn
    from .config import settings

    uvicorn.run("xhs_workflow.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    main()