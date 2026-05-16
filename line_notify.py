import logging
import os

import requests

logger = logging.getLogger(__name__)

_LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"


def send_group_signal(message: str, token: str = None) -> bool:
    """Broadcast a message to the LINE group via LINE Notify.

    Returns True on success, False otherwise.
    Token defaults to the LINE_NOTIFY_TOKEN environment variable.
    """
    token = token or os.environ.get("LINE_NOTIFY_TOKEN")
    if not token:
        logger.warning("LINE_NOTIFY_TOKEN not set — skipping LINE Notify broadcast")
        return False
    try:
        resp = requests.post(
            _LINE_NOTIFY_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={"message": message},
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("LINE Notify broadcast sent successfully")
            return True
        logger.error("LINE Notify failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("LINE Notify error: %s", e)
        return False
