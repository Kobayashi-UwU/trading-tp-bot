import hashlib
import hmac
import logging
import os

import requests

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v20.0"


def _is_enabled() -> bool:
    return bool(os.environ.get("FB_PAGE_ACCESS_TOKEN"))


def _token() -> str:
    return os.environ["FB_PAGE_ACCESS_TOKEN"]


# ---------------------------------------------------------------------------
# Outgoing messages
# ---------------------------------------------------------------------------

def fb_send(psid: str, text: str) -> None:
    """Send a plain-text message to a Messenger user."""
    if not _is_enabled():
        logger.warning("FB_PAGE_ACCESS_TOKEN not set — fb_send skipped")
        return
    resp = requests.post(
        f"{_GRAPH_BASE}/me/messages",
        params={"access_token": _token()},
        json={"recipient": {"id": psid}, "message": {"text": text}},
        timeout=10,
    )
    resp.raise_for_status()


def fb_send_recurring_opt_in(psid: str) -> None:
    """Ask the user to send a message so we can keep the 24-hour window open.

    Note: Facebook Recurring Notifications API (template_type=notification_messages)
    requires a separate feature approval (error_subcode 2018012). Until that is
    approved, we send a plain-text prompt instead.
    """
    fb_send(
        psid,
        "📬 พิมพ์ /signal เพื่อดู signal ทองคำประจำวันได้เลยครับ (วันละ 1 ครั้ง) 📊",
    )


_ENGAGED_SENTINEL = "ENGAGED"


def fb_push(psid: str, text: str, notification_token: str | None = None) -> None:
    """Push a message to a user.

    Uses the Recurring Notifications token when available (works outside the
    24-hour window). Falls back to a regular send otherwise.

    notification_token == ENGAGED_SENTINEL means the user has engaged with the
    bot and we should use regular send (within 24-hour window only).
    """
    if not _is_enabled():
        logger.warning("FB_PAGE_ACCESS_TOKEN not set — fb_push skipped")
        return
    if notification_token and notification_token != _ENGAGED_SENTINEL:
        resp = requests.post(
            f"{_GRAPH_BASE}/me/messages",
            params={"access_token": _token()},
            json={
                "recipient": {"notification_messages_token": notification_token},
                "message": {"text": text},
                "messaging_type": "MESSAGE_TAG",
                "tag": "NOTIFICATION_MESSAGES",
            },
            timeout=10,
        )
    else:
        resp = requests.post(
            f"{_GRAPH_BASE}/me/messages",
            params={"access_token": _token()},
            json={"recipient": {"id": psid}, "message": {"text": text}},
            timeout=10,
        )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

def get_fb_profile(psid: str) -> str:
    """Return the user's display name, or empty string on failure."""
    if not _is_enabled():
        return ""
    try:
        resp = requests.get(
            f"{_GRAPH_BASE}/{psid}",
            params={"fields": "name", "access_token": _token()},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("get_fb_profile failed psid=%s status=%s body=%s", psid, resp.status_code, resp.text[:200])
            return ""
        data = resp.json()
        if "error" in data:
            logger.warning("get_fb_profile API error psid=%s: %s", psid, data["error"])
            return ""
        return data.get("name", "")
    except Exception as e:
        logger.warning("get_fb_profile exception psid=%s: %s", psid, e)
        return ""


# ---------------------------------------------------------------------------
# Handover Protocol
# ---------------------------------------------------------------------------

def take_thread_control(psid: str) -> bool:
    """Take thread control back from inbox/secondary receiver.

    Must be called by the Primary Receiver app. Fails silently when our app
    is not the Primary Receiver (returns False).
    """
    if not _is_enabled():
        return False
    try:
        resp = requests.post(
            f"{_GRAPH_BASE}/me/take_thread_control",
            params={"access_token": _token()},
            json={"recipient": {"id": psid}, "metadata": "bot"},
            timeout=10,
        )
        if not resp.ok:
            logger.debug("take_thread_control failed psid=%s: %s", psid, resp.text)
        return resp.ok
    except Exception as e:
        logger.debug("take_thread_control error: %s", e)
        return False


def set_as_primary_receiver(app_id: str) -> dict:
    """Register this app as Primary Receiver in Handover Protocol.

    Call once via the /setup/facebook endpoint. Requires FB_APP_ID env var.
    """
    resp = requests.post(
        f"{_GRAPH_BASE}/me/messenger_profile",
        params={"access_token": _token()},
        json={"handover_protocol": {"primary_receiver_app_id": app_id}},
        timeout=10,
    )
    return {"status": resp.status_code, "body": resp.json()}


# ---------------------------------------------------------------------------
# Webhook security
# ---------------------------------------------------------------------------

def verify_fb_signature(payload: bytes, signature_header: str) -> bool:
    """Validate the X-Hub-Signature-256 header sent by Facebook."""
    secret = os.environ.get("FB_APP_SECRET", "")
    if not secret:
        return True  # signature check disabled when secret not configured
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature_header or "")
