from __future__ import annotations

import re


def has_keyword(haystack: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    if re.fullmatch(r"[a-z0-9 .+#_-]+", keyword):
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        return re.search(pattern, haystack) is not None
    return keyword in haystack


def ai_score(text: str, keywords: list[str]) -> float:
    haystack = text.lower()
    hits = sum(1 for keyword in keywords if has_keyword(haystack, keyword))
    return min(1.0, hits / max(1, min(5, len(keywords))))


def community_ai_score(subreddit: str, keywords: list[str]) -> float:
    community = subreddit.lower().replace("_", " ")
    score = ai_score(community, keywords)
    if subreddit.lower() in {"artificial", "machinelearning", "localllama", "openai", "chatgpt", "singularity"}:
        score = max(score, 0.35)
    return score


def heat_label(score: float) -> str:
    if score >= 8:
        return "爆发"
    if score >= 5:
        return "升温"
    return "稳定"
