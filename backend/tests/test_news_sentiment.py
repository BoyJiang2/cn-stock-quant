from app.data.news_sentiment import classify_news_text


def test_classify_news_text_marks_severe_negative_events():
    event_type, label, score = classify_news_text("公司被立案调查", "可能面临处罚")

    assert event_type == "negative_news"
    assert label == "negative"
    assert score == -0.8


def test_classify_news_text_marks_risk_events():
    event_type, label, score = classify_news_text("股东计划减持股份")

    assert event_type == "risk_news"
    assert label == "risk"
    assert score == -0.4


def test_classify_news_text_leaves_unmatched_news_unlabeled():
    event_type, label, score = classify_news_text("公司发布新产品")

    assert event_type == "stock_news"
    assert label == ""
    assert score is None
