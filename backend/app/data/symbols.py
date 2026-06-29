import re

INDEX_SYMBOL_WHITELIST = frozenset({"000300", "000905", "000852", "399006"})


def normalize_a_share_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if not value:
        raise ValueError("symbol is required")

    value = value.replace(".", "")
    value = re.sub(r"^(SH|SZ|BJ)", "", value)
    value = re.sub(r"(SH|SZ|BJ)$", "", value)

    if value.isdigit() and len(value) < 6:
        value = value.zfill(6)

    if not re.fullmatch(r"\d{6}", value):
        raise ValueError(f"invalid A-share symbol: {symbol}")
    return value


def normalize_a_share_symbols(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        value = normalize_a_share_symbol(symbol)
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized
