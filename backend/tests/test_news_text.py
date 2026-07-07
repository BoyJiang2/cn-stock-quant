from app.data.news_text import clean_news_payload, clean_news_text, has_mojibake


def test_clean_news_text_repairs_utf8_latin1_mojibake():
    dirty = "å½å®¶å¤§åºéæè¡æ¦å¿µä¸è·6.28%ï¼ä¸»åèµéåæµåº37è¡"

    cleaned = clean_news_text(dirty)

    assert cleaned == "国家大基金持股概念下跌6.28%，主力资金净流出37股"
    assert not has_mojibake(cleaned)


def test_clean_news_text_keeps_normal_text_unchanged():
    text = "通富微电获得机构买入，成交量放大"

    assert clean_news_text(text) == text


def test_clean_news_payload_recursively_repairs_strings():
    payload = {"æ°é»æ é¢": ["çµå­è¡ä¸ä¸æ¶¨"]}

    assert clean_news_payload(payload) == {"新闻标题": ["电子行业上涨"]}
