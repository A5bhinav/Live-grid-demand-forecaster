"""Alert delivery to a Slack or Discord incoming webhook.

    uv run python -m gridcast.alerts "message"

Without ALERT_WEBHOOK_URL nothing is sent; GitHub's failure email is then the only alert.
"""

import sys

import httpx

from gridcast import config


def send_alert(text: str, webhook_url: str | None, client: httpx.Client | None = None) -> bool:
    """Post `text` to the webhook. Returns whether anything was sent."""
    if not webhook_url:
        return False
    # Discord reads `content`; Slack and most compatible services read `text`.
    key = "content" if "discord" in httpx.URL(webhook_url).host else "text"
    response = (client or httpx.Client(timeout=20)).post(webhook_url, json={key: text})
    response.raise_for_status()
    return True


def main() -> None:
    url = config.Settings().alert_webhook_url
    sent = send_alert(" ".join(sys.argv[1:]), url.get_secret_value() if url else None)
    print("alert sent" if sent else "no webhook configured; nothing sent")


if __name__ == "__main__":
    main()
