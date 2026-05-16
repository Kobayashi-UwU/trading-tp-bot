import logging
import os

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)

logger = logging.getLogger(__name__)


def send_group_signal(message: str) -> bool:
    """Broadcast a message to the LINE group using the LINE Messaging API.

    Requires LINE_BROADCAST_GROUP_ID and LINE_CHANNEL_ACCESS_TOKEN env vars.
    Returns True on success, False otherwise.
    """
    group_id = os.environ.get("LINE_BROADCAST_GROUP_ID")
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

    if not group_id:
        logger.warning("LINE_BROADCAST_GROUP_ID not set — skipping group broadcast")
        return False
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set — skipping group broadcast")
        return False

    try:
        config = Configuration(access_token=token)
        with ApiClient(config) as client:
            MessagingApi(client).push_message(
                PushMessageRequest(to=group_id, messages=[TextMessage(text=message)])
            )
        logger.info("LINE group broadcast sent to group %s", group_id)
        return True
    except Exception as e:
        logger.error("LINE group push failed: %s", e)
        return False
