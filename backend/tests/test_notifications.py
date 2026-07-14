import json
from unittest.mock import patch
from urllib.error import URLError

import pytest

from app.notifications import NotificationDeliveryError, WeComGroupWebhookSender


WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key"


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return self.body


@pytest.mark.parametrize(
    "webhook_url",
    [
        "",
        "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        "https://",
        "https:// qyapi.weixin.qq.com/hook",
        "https://user:password@qyapi.weixin.qq.com/hook",
        "https://qyapi.weixin.qq.com",
    ],
)
def test_wecom_sender_rejects_invalid_webhook_url(webhook_url: str):
    with pytest.raises(ValueError):
        WeComGroupWebhookSender(webhook_url)


def test_wecom_sender_rejects_empty_text_without_network_call():
    sender = WeComGroupWebhookSender(WEBHOOK_URL)

    with patch("app.notifications.wecom.urlopen") as mock_urlopen:
        with pytest.raises(ValueError, match="must not be empty"):
            sender.send_text("   ")

    mock_urlopen.assert_not_called()


def test_wecom_sender_posts_text_payload_and_returns_receipt():
    sender = WeComGroupWebhookSender(WEBHOOK_URL, timeout_seconds=2.5)

    with patch(
        "app.notifications.wecom.urlopen",
        return_value=FakeResponse({"errcode": 0, "errmsg": "ok"}),
    ) as mock_urlopen:
        receipt = sender.send_text("组合风险提示")

    request = mock_urlopen.call_args.args[0]
    assert request.full_url == WEBHOOK_URL
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {
        "msgtype": "text",
        "text": {"content": "组合风险提示"},
    }
    assert mock_urlopen.call_args.kwargs["timeout"] == 2.5
    assert receipt.channel == "wecom_group_webhook"
    assert receipt.provider_message == "ok"


def test_wecom_sender_surfaces_provider_rejection():
    sender = WeComGroupWebhookSender(WEBHOOK_URL)

    with patch(
        "app.notifications.wecom.urlopen",
        return_value=FakeResponse({"errcode": 93000, "errmsg": "invalid webhook"}),
    ):
        with pytest.raises(NotificationDeliveryError, match="93000.*invalid webhook"):
            sender.send_text("组合风险提示")


def test_wecom_sender_surfaces_network_error():
    sender = WeComGroupWebhookSender(WEBHOOK_URL)

    with patch(
        "app.notifications.wecom.urlopen",
        side_effect=URLError("connection refused"),
    ):
        with pytest.raises(NotificationDeliveryError, match="connection refused"):
            sender.send_text("组合风险提示")
