import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.notifications.base import NotificationDeliveryError, NotificationReceipt


class WeComGroupWebhookSender:
    """Send text-only messages to an Enterprise WeChat group webhook."""

    channel = "wecom_group_webhook"

    def __init__(self, webhook_url: str, timeout_seconds: float = 5.0) -> None:
        self.webhook_url = self._validate_webhook_url(webhook_url)
        if timeout_seconds <= 0:
            raise ValueError("notification timeout must be positive")
        self.timeout_seconds = timeout_seconds

    def send_text(self, text: str) -> NotificationReceipt:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("notification text must not be empty")

        payload = json.dumps(
            {"msgtype": "text", "text": {"content": text}},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise NotificationDeliveryError(
                f"WeCom webhook returned HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise NotificationDeliveryError(
                f"WeCom webhook request failed: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise NotificationDeliveryError(
                f"WeCom webhook request failed: {exc}"
            ) from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise NotificationDeliveryError("WeCom webhook returned invalid JSON") from exc

        if not isinstance(body, dict):
            raise NotificationDeliveryError("WeCom webhook returned an invalid response")

        error_code = body.get("errcode")
        if error_code != 0:
            message = body.get("errmsg") or "unknown error"
            raise NotificationDeliveryError(
                f"WeCom webhook rejected the message ({error_code}): {message}"
            )

        return NotificationReceipt(
            channel=self.channel,
            provider_message=str(body.get("errmsg") or "ok"),
        )

    @staticmethod
    def _validate_webhook_url(webhook_url: str) -> str:
        if not isinstance(webhook_url, str) or not webhook_url:
            raise ValueError("WeCom webhook URL must not be empty")
        if webhook_url != webhook_url.strip() or any(char.isspace() for char in webhook_url):
            raise ValueError("WeCom webhook URL must not contain whitespace")

        try:
            parsed = urlparse(webhook_url)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("WeCom webhook URL is malformed") from exc

        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not parsed.hostname
            or not parsed.path
            or parsed.username
            or parsed.password
        ):
            raise ValueError("WeCom webhook URL must be a valid HTTPS URL")
        return webhook_url
