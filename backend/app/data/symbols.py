import re


def normalize_a_share_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not value:
        raise ValueError("symbol is required")

    value = value.replace(".", "")
    value = re.sub(r"^(SH|SZ|BJ)", "", value)
    value = re.sub(r"(SH|SZ|BJ)$", "", value)

    if not re.fullmatch(r"\d{6}", value):
        raise ValueError(f"invalid A-share symbol: {symbol}")
    return value

