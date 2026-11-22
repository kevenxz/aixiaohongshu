from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Optional

import requests

from .config import Settings
from .models import Draft, SourceItem

if TYPE_CHECKING:
    from .accounts import AccountConfig

log = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 12000
DIVERSION_PHRASES = (
    "去看原", "去原视频", "观看原视频", "点击观看", "点开视频", "去视频里",
    "阅读原文", "查看原帖", "详见原文", "建议直接去", "看原帖", "看原视频",
)
GALLERY_RISK_FLAG = "图片来自微博/Instagram，发布前确认授权与肖像使用范围"


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response is not a JSON object")
    return value


class LLM:
    def __init__(self, settings: Settings):
        self.s = settings
        self._throttle_lock = threading.Lock()
        self._last_request = 0.0

    def _throttle(self) -> None:
        with self._throttle_lock:
            wait = self.s.llm_throttle_seconds - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _request(self, payload: dict) -> dict:
        url = f"{self.s.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}", "Content-Type": "application/json"}
        attempts = max(1, self.s.llm_max_retries)
        for attempt in range(attempts):
            self._throttle()
            try:
                res = requests.post(url, headers=headers, json=payload, timeout=90)
                if res.status_code in (400, 422) and "response_format" in payload:
                    payload = {key: value for key, value in payload.items() if key != "response_format"}
                    self._throttle()
                    res = requests.post(url, headers=headers, json=payload, timeout=90)
            except requests.RequestException as exc:
                if attempt + 1 >= attempts:
                    raise
                delay = min(20, 2 ** (attempt + 1))
                log.warning("LLM request failed (%s), retrying in %ss", type(exc).__name__, delay)
                time.sleep(delay)
                continue

            if res.status_code == 429 or 500 <= res.status_code < 600:
                if attempt + 1 >= attempts:
                    res.raise_for_status()
                retry_after = res.headers.get("Retry-After", "")
                try:
                    delay = min(30, max(1, int(retry_after)))
                except ValueError:
                    delay = min(20, 2 ** (attempt + 1))
                log.warning("LLM returned %s, retrying in %ss", res.status_code, delay)
                time.sleep(delay)
                continue

            res.raise_for_status()
            return res.json()
        raise RuntimeError("LLM request failed after retries")

    def transform(self, item: SourceItem, account: Optional["AccountConfig"] = None) -> Draft:
        if not self.s.llm_api_key or not self.s.llm_model:
            raise RuntimeError("LLM_API_KEY 或 LLM_MODEL 未配置")
        draft = self._transform_once(item, retry_for_diversion=False, account=account)
        if _contains_diversion(draft.body):
            draft = self._transform_once(item, retry_for_diversion=True, account=account)
        return draft

    def _transform_once(self, item: SourceItem, retry_for_diversion: bool,
                        account: Optional["AccountConfig"] = None) -> Draft:
        gallery = bool(account and account.is_gallery)
        retry_rule = (
            "\n重要：你上一次的输出包含让读者去看原帖/原视频/原图的句子。禁止任何导流；"
            "只使用已提供的内容完整翻译改写。信息不足就明确说明，不要叫读者跳转。\n"
            if retry_for_diversion else ""
        )
        persona = (account.prompt.persona if account else "") or (
            "小红书明星图集编辑" if gallery else "中文小红书图文编辑"
        )
        extra_rules = account.prompt.extra_rules if account else ""
        extra_block = f"\n账号额外要求：{extra_rules}\n" if extra_rules else ""

        if gallery:
            prompt = f"""把下面的来源配文整理成一篇可直接发布的小红书明星图集笔记。
{retry_rule}{extra_block}
要求：
- 正文必须是完整的中文图集文案，不是摘要，也不是导流；不要写“点击原图/去看原帖”等句子。
- 标题总长度不超过 20 个字符（中英文、数字、标点各算 1 个），包含明星名 + 事件/造型/作品。
- 正文 100-300 字，客观描述造型、场合、作品、氛围和亮点；不要编造来源没有的事实。
- 不传播未经证实的恋情、绯闻、私生活；不确定的信息写入 risk_flags。
- 不输出 URL；来源由系统单独展示。
- tags 使用不带 # 的短词，5 到 8 个：明星名、作品、风格、场合。
- 输出严格 JSON：title（字符串）、body（字符串）、tags（字符串数组）、risk_flags（字符串数组）。

平台：{item.platform}
作者：{item.author}
来源：{item.source_url}
原文标题：{item.title}
配文：{item.text[:MAX_SOURCE_CHARS]}
"""
        else:
            prompt = f"""把下面的来源内容翻译并改写成一篇可直接发布的小红书图文笔记。
{retry_rule}{extra_block}
要求：
- 正文必须是完整的中文笔记，不是摘要，也不是导流；不要写“点击原视频/原帖”“建议去看原文”等句子。
- 完整保留来源的核心信息、关键数字、观点和限定条件；不要只复述标题。
- 英文内容翻译成自然中文，不要逐句直译。
- 不要输出来源 URL；来源链接由系统单独展示。
- 信息不足时，明确写出已知信息和不确定点，但不要用“去看原文”代替正文。
- 观点归因给来源，例如“据 OpenAI 官方博客”，不要冒充原创。
- 标题总长度不超过 20 个字符（中英文、数字、标点各算 1 个）；正文 300-800 字，开头有钩子，结尾有一个互动问题。
- 医疗、投资、政治、争议、传闻和未经证实的信息写入 risk_flags。
- tags 使用不带 # 的短词，3 到 5 个。
输出严格 JSON：title（字符串）、body（字符串）、tags（字符串数组）、risk_flags（字符串数组）。

平台：{item.platform}
作者：{item.author}
来源：{item.source_url}
原文标题：{item.title}
原文：{item.text[:MAX_SOURCE_CHARS]}
"""
        data = self._request({
            "model": self.s.llm_model,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"你是{persona}。你的任务是把来源内容完整整理成可直接发布的笔记，"
                        "不是摘要、不是导流。绝不引导读者去看原视频/原帖/原图。不得编造来源没有的事实。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        })
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_json_object(content)
        body = str(parsed.get("body", "")).strip()
        if not body:
            raise RuntimeError("模型返回空正文")
        tags = []
        for raw in parsed.get("tags", []):
            tag = str(raw).strip().lstrip("#")
            if tag and tag not in tags:
                tags.append(tag)
        risk_flags = [str(flag) for flag in parsed.get("risk_flags", []) if str(flag).strip()]
        if gallery and GALLERY_RISK_FLAG not in risk_flags:
            risk_flags.append(GALLERY_RISK_FLAG)
        return Draft(
            title=str(parsed.get("title") or item.title or ("明星图集" if gallery else "海外内容整理")).strip(),
            body=body,
            tags=tags[:8] if gallery else tags[:5],
            source_note=f"来源：{item.source_url}",
            risk_flags=risk_flags,
        )


def _contains_diversion(body: str) -> bool:
    return any(phrase in body for phrase in DIVERSION_PHRASES)