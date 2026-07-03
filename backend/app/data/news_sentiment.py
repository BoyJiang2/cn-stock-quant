from __future__ import annotations


SEVERE_NEGATIVE_KEYWORDS = (
    "立案",
    "调查",
    "处罚",
    "退市",
    "违约",
    "债务逾期",
    "资不抵债",
    "破产",
    "重大亏损",
    "预亏",
    "诉讼",
    "仲裁",
)

RISK_KEYWORDS = (
    "减持",
    "风险提示",
    "监管函",
    "问询函",
    "警示函",
    "亏损",
    "下跌",
    "净流出",
    "跌停",
    "异动",
)


def classify_news_text(title: str, body: str = "") -> tuple[str, str, float | None]:
    text = f"{title} {body}"
    if any(keyword in text for keyword in SEVERE_NEGATIVE_KEYWORDS):
        return "negative_news", "negative", -0.8
    if any(keyword in text for keyword in RISK_KEYWORDS):
        return "risk_news", "risk", -0.4
    return "stock_news", "", None
