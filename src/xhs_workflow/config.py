from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _list_env(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    app_username: str = os.getenv("APP_USERNAME", "admin")
    app_password: str = os.getenv("APP_PASSWORD", "")
    app_media_token: str = os.getenv("APP_MEDIA_TOKEN", "")
    app_timezone: str = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
    database_path: Path = Path(os.getenv("DATABASE_PATH", "data/workflow.db"))
    media_dir: Path = Path(os.getenv("MEDIA_DIR", "data/media"))
    export_dir: Path = Path(os.getenv("EXPORT_DIR", "data/exports"))
    accounts_file: Path = Path(os.getenv("ACCOUNTS_FILE", "config/accounts.yaml"))
    target_posts_per_day: int = int(os.getenv("TARGET_POSTS_PER_DAY", "30"))
    collect_cron: str = os.getenv("COLLECT_CRON", "0 8 * * *")
    fetch_per_provider: int = int(os.getenv("FETCH_PER_PROVIDER", "20"))
    ai_keywords: list[str] = None  # type: ignore[assignment]
    ai_only: bool = os.getenv("AI_ONLY", "true").lower() == "true"
    min_ai_score: float = float(os.getenv("MIN_AI_SCORE", "0.15"))

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_throttle_seconds: float = float(os.getenv("LLM_THROTTLE_SECONDS", "1.5"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "4"))

    reddit_client_id: str = os.getenv("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    reddit_user_agent: str = os.getenv("REDDIT_USER_AGENT", "xhs-workflow/0.2")
    reddit_subreddits: list[str] = None  # type: ignore[assignment]
    reddit_sort: str = os.getenv("REDDIT_SORT", "hot")
    reddit_top_time: str = os.getenv("REDDIT_TOP_TIME", "day")
    reddit_mode: str = os.getenv("REDDIT_MODE", "auto").lower()
    rss_feeds: list[str] = None  # type: ignore[assignment]

    x_bearer_token: str = os.getenv("X_BEARER_TOKEN", "")
    x_queries: list[str] = None  # type: ignore[assignment]
    x_list_id: str = os.getenv("X_LIST_ID", "")
    x_priority_accounts: list[str] = None  # type: ignore[assignment]
    x_priority_boost: float = float(os.getenv("X_PRIORITY_BOOST", "4"))
    x_priority_all: bool = os.getenv("X_PRIORITY_ALL", "true").lower() == "true"
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    youtube_queries: list[str] = None  # type: ignore[assignment]
    youtube_region_code: str = os.getenv("YOUTUBE_REGION_CODE", "US")
    youtube_days: int = int(os.getenv("YOUTUBE_DAYS", "7"))
    youtube_video_duration: str = os.getenv("YOUTUBE_VIDEO_DURATION", "medium")
    youtube_language: str = os.getenv("YOUTUBE_LANGUAGE", "en")

    allow_unverified_repost: bool = os.getenv("ALLOW_UNVERIFIED_REPOST", "false").lower() == "true"
    media_timeout_seconds: int = int(os.getenv("MEDIA_TIMEOUT_SECONDS", "20"))
    media_max_bytes: int = int(os.getenv("MEDIA_MAX_BYTES", str(10 * 1024 * 1024)))
    publisher: str = os.getenv("PUBLISHER", "manual")
    publish_webhook_url: str = os.getenv("PUBLISH_WEBHOOK_URL", "")
    mcp_server_url: str = os.getenv("XHS_MCP_SERVER_URL", "")
    mcp_tool_name: str = os.getenv("XHS_MCP_TOOL_NAME", "publish_content")
    mcp_auth_token: str = os.getenv("XHS_MCP_AUTH_TOKEN", "")
    xhs_visibility: str = os.getenv("XHS_MCP_VISIBILITY", "公开可见")
    xhs_media_base_url: str = os.getenv("XHS_MEDIA_BASE_URL", "")
    auto_publish: bool = os.getenv("AUTO_PUBLISH", "false").lower() == "true"
    auto_publish_cron: str = os.getenv("AUTO_PUBLISH_CRON", "30 9 * * *")
    auto_publish_limit: int = int(os.getenv("AUTO_PUBLISH_LIMIT", "3"))

    def __post_init__(self) -> None:
        if self.ai_keywords is None:
            object.__setattr__(self, "ai_keywords", _list_env("AI_KEYWORDS", "ai,artificial intelligence,machine learning,deep learning,llm,generative ai,chatgpt,openai,claude,gemini,anthropic,人工智能,大模型,生成式 ai,机器学习"))
        if self.reddit_subreddits is None:
            object.__setattr__(self, "reddit_subreddits", _list_env("REDDIT_SUBREDDITS", "artificial,MachineLearning,LocalLLaMA,OpenAI,ChatGPT"))
        if self.reddit_mode not in ("auto", "api", "rss"):
            object.__setattr__(self, "reddit_mode", "auto")
        if self.rss_feeds is None:
            object.__setattr__(self, "rss_feeds", _list_env("RSS_FEEDS", ""))
        if self.x_queries is None:
            object.__setattr__(self, "x_queries", _list_env("X_QUERIES", "AI OR artificial intelligence,LLM,ChatGPT OR OpenAI,Claude OR Gemini"))
        accounts = self.x_priority_accounts if self.x_priority_accounts is not None else _list_env("X_PRIORITY_ACCOUNTS", "")
        object.__setattr__(
            self, "x_priority_accounts",
            [handle.strip().lstrip("@") for handle in accounts if handle.strip()],
        )
        list_id = self.x_list_id.strip()
        if "/lists/" in list_id:
            list_id = list_id.rsplit("/lists/", 1)[-1].split("?")[0].strip("/")
        object.__setattr__(self, "x_list_id", list_id)
        if self.youtube_queries is None:
            object.__setattr__(self, "youtube_queries", _list_env("YOUTUBE_QUERIES", "AI,artificial intelligence,machine learning,large language model"))
        for path in (self.database_path.parent, self.media_dir, self.export_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
