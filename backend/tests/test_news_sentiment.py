import pytest

from app.data.news_sentiment import classify_news_text


@pytest.mark.parametrize(
    ("title", "body", "expected_event_type", "expected_label", "expected_score"),
    [
        ("公司被立案调查", "", "severe_company_risk", "negative", -0.8),
        ("", "公司因证监会立案披露进展", "severe_company_risk", "negative", -0.8),
        ("股东计划减持股份", "", "company_risk", "risk", -0.4),
        ("", "公司收到监管函", "company_risk", "risk", -0.4),
        ("板块资金净流出", "", "industry_market_flow", "", None),
        ("", "主力资金净流出", "industry_market_flow", "", None),
        ("市场出现跌停", "", "general_market_sentiment", "", None),
        ("", "股价异动后下跌", "general_market_sentiment", "", None),
    ],
)
def test_classify_news_text_matches_risk_keywords_in_title_or_body(
    title: str,
    body: str,
    expected_event_type: str,
    expected_label: str,
    expected_score: float | None,
):
    event_type, label, score = classify_news_text(title, body)

    assert event_type == expected_event_type
    assert label == expected_label
    assert score == expected_score


@pytest.mark.parametrize(
    ("title", "body", "expected_event_type", "expected_label", "expected_score"),
    [
        ("股东减持", "公司被立案调查", "severe_company_risk", "negative", -0.8),
        ("股东减持", "板块资金净流出，市场跌停", "company_risk", "risk", -0.4),
        ("板块资金净流出", "市场跌停", "industry_market_flow", "", None),
    ],
)
def test_classify_news_text_uses_fixed_risk_priority(
    title: str,
    body: str,
    expected_event_type: str,
    expected_label: str,
    expected_score: float | None,
):
    event_type, label, score = classify_news_text(title, body)

    assert event_type == expected_event_type
    assert label == expected_label
    assert score == expected_score


@pytest.mark.parametrize(
    "title",
    [
        "预亏变预盈，业绩大幅改善",
        "公司晚间传来利好，异动拉升涨停",
        "退市新规出台，市场解读",
        "减持新规正式落地",
        "调查显示行业景气度回升",
    ],
)
def test_classify_news_text_excludes_non_company_risk_cues(title: str):
    event_type, label, score = classify_news_text(title)

    assert event_type == "neutral"
    assert label == ""
    assert score is None


def test_classify_news_text_leaves_unmatched_news_unlabeled():
    event_type, label, score = classify_news_text("公司发布新产品")

    assert event_type == "neutral"
    assert label == ""
    assert score is None
