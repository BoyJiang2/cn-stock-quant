from __future__ import annotations


SEVERE_COMPANY_RISK_KEYWORDS = (
    "立案调查",
    "证监会立案",
    "行政处罚",
    "处罚决定",
    "财务造假",
    "债务违约",
    "债务逾期",
    "资不抵债",
    "破产",
    "破产重整",
    "终止上市",
    "强制退市",
)

COMPANY_RISK_KEYWORDS = (
    "减持",
    "风险提示",
    "监管函",
    "问询函",
    "警示函",
    "亏损",
)

INDUSTRY_MARKET_FLOW_KEYWORDS = (
    "净流出",
)

GENERAL_MARKET_SENTIMENT_KEYWORDS = (
    "下跌",
    "跌停",
    "异动",
)

NON_COMPANY_RISK_CUES = (
    "预亏变预盈",
    "亏损收窄",
    "扭亏为盈",
    "胜诉",
    "解除风险",
    "调查显示",
    "退市新规",
    "减持新规",
    "异动拉升",
    "涨停",
    "利好",
)


def classify_news_text(title: str, body: str = "") -> tuple[str, str, float | None]:
    """Classify news with a fixed, highest-risk-first event priority."""
    text = f"{title} {body}"
    if any(keyword in text for keyword in NON_COMPANY_RISK_CUES):
        return "neutral", "", None
    if any(keyword in text for keyword in SEVERE_COMPANY_RISK_KEYWORDS):
        return "severe_company_risk", "negative", -0.8
    if any(keyword in text for keyword in COMPANY_RISK_KEYWORDS):
        return "company_risk", "risk", -0.4
    if any(keyword in text for keyword in INDUSTRY_MARKET_FLOW_KEYWORDS):
        return "industry_market_flow", "", None
    if any(keyword in text for keyword in GENERAL_MARKET_SENTIMENT_KEYWORDS):
        return "general_market_sentiment", "", None
    return "neutral", "", None
