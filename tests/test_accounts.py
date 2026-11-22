from __future__ import annotations

import dataclasses

from xhs_workflow.accounts import load_accounts


def test_loads_yaml_accounts_with_env_defaults(tmp_path, test_settings, monkeypatch):
    monkeypatch.setenv("TEST_STAR_MCP", "http://mcp.star/mcp")
    config = tmp_path / "accounts.yaml"
    config.write_text(
        """
accounts:
  - slug: ai-signals
    name: AI 情报站
  - slug: star-gallery
    name: 明星图集
    content_style: gallery
    target_posts_per_day: 6
    media:
      rights_mode: editorial
      max_images: 9
      min_images: 3
    sources:
      weibo:
        uids:
          - uid: "123"
            name: 明星A
      instagram:
        feeds:
          - name: 明星A
            url: https://example.com/feed.xml
    publish:
      mode: mcp
      mcp_url: ${TEST_STAR_MCP}
      token_env: TEST_STAR_TOKEN
        """,
        encoding="utf-8",
    )
    settings = dataclasses.replace(test_settings, accounts_file=config)
    accounts = load_accounts(settings)
    assert [account.slug for account in accounts] == ["ai-signals", "star-gallery"]
    star = accounts[1]
    assert star.is_gallery is True
    assert star.platforms == ["weibo", "instagram"]
    assert star.media.max_images == 9
    assert star.publish.mcp_url == "http://mcp.star/mcp"
    assert star.target_posts_per_day == 6


def test_missing_accounts_file_falls_back_to_legacy(tmp_path, test_settings):
    settings = dataclasses.replace(test_settings, accounts_file=tmp_path / "missing.yaml")
    accounts = load_accounts(settings)
    assert len(accounts) == 1
    assert accounts[0].slug == "ai-signals"
    assert "reddit" in accounts[0].platforms


def test_env_default_syntax(tmp_path, test_settings, monkeypatch):
    monkeypatch.delenv("MISSING_STAR_URL", raising=False)
    config = tmp_path / "accounts.yaml"
    config.write_text(
        """
accounts:
  - slug: star-gallery
    name: 明星图集
    content_style: gallery
    sources:
      weibo:
        uids: []
    publish:
      mode: mcp
      mcp_url: ${MISSING_STAR_URL:-http://127.0.0.1:18061/mcp}
        """,
        encoding="utf-8",
    )
    settings = dataclasses.replace(test_settings, accounts_file=config)
    account = load_accounts(settings)[0]
    assert account.publish.mcp_url == "http://127.0.0.1:18061/mcp"