from dataclasses import dataclass
from typing import Protocol


class NotificationDeliveryError(RuntimeError):
    """Raised when a notification could not be delivered."""


@dataclass(frozen=True)
class NotificationReceipt:
    channel: str
    provider_message: str


class NotificationSender(Protocol):
    """Outbound-only notification sender."""

    def send_text(self, text: str) -> NotificationReceipt:
        """Deliver one plain-text notification."""
