from __future__ import annotations

import re

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")


def sanitize(name: str, max_len: int = 120) -> str:
    cleaned = _ILLEGAL.sub("_", name)
    cleaned = _WS.sub(" ", cleaned).strip()
    if not cleaned:
        return "untitled"
    if cleaned in (".", ".."):
        return "untitled"
    return cleaned[:max_len].strip()


def stem(video_id: str, name: str) -> str:
    return f"{video_id}__{sanitize(name)}"
