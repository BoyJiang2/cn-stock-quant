from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_MOJIBAKE_MARKERS = ("Ã", "Â", "â", "å", "æ", "ç", "è", "é", "ï", "¼", "½", "ä", "ð")
_CONTROL_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_news_text(value: object) -> str:
    """Return display/model-safe news text.

    Some AkShare/Eastmoney responses arrive as UTF-8 bytes decoded through a
    Latin-1/Windows-1252 lens, e.g. ``å½å®¶`` instead of ``国家``.  The repair is
    deliberately score-based so normal Chinese and English text is left alone.
    """
    if value is None:
        return ""
    text = str(value)
    text = _repair_mojibake(text)
    text = _CONTROL_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_news_payload(value: Any) -> Any:
    """Recursively clean strings inside a raw provider payload."""
    if isinstance(value, Mapping):
        return {clean_news_text(key): clean_news_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_news_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(clean_news_payload(item) for item in value)
    if isinstance(value, str):
        return clean_news_text(value)
    return value


def has_mojibake(value: object) -> bool:
    if value is None:
        return False
    text = str(value)
    return _mojibake_penalty(text) >= 2


def _repair_mojibake(text: str) -> str:
    candidates = [text]
    for encoding in ("latin1", "cp1252"):
        try:
            decoded = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        if decoded not in candidates:
            candidates.append(decoded)
    return max(candidates, key=_text_score)


def _text_score(text: str) -> int:
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    replacement_count = text.count("\ufffd")
    return cjk_count * 3 - _mojibake_penalty(text) * 4 - replacement_count * 8


def _mojibake_penalty(text: str) -> int:
    marker_count = sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)
    control_count = sum(1 for char in text if "\u0080" <= char <= "\u009f")
    return marker_count + control_count * 2
