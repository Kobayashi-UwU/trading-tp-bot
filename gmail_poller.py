import email
import imaplib
import logging
import os
import re

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)

logger = logging.getLogger(__name__)

_IMAP_HOST = "imap.gmail.com"
_IMAP_PORT = 993
_IUX_SENDER = "noreply@iux.com"

_VERIFY_MSG = (
    "🎉 ยืนยันเรียบร้อยแล้วครับ!\n\n"
    "กลุ่มไลน์: https://line.me/ti/g2/2qPd6fIG5bY4P04_uKo_0sLKLDvqqTsAILh5Qg"
    "?utm_source=invitation&utm_medium=link_copy&utm_campaign=default\n\n"
    "คุณจะได้รับ Daily Trading Signal ทุกเช้า 8:00 น.📈\n\n"
    "Strategy / Pine Script\n"
    "https://github.com/Kobayashi-UwU/trading_tp/tree/main/strategy\n\n"
    "Prompt\n"
    "https://github.com/Kobayashi-UwU/trading_tp/tree/main/prompt\n\n"
    "Code\n"
    "https://github.com/Kobayashi-UwU/trading_tp/tree/main/code"
)


def _is_enabled() -> bool:
    return bool(os.environ.get("GMAIL_ADDRESS") and os.environ.get("GMAIL_APP_PASSWORD"))


def _extract_iux_id(text: str) -> str | None:
    match = re.search(r"User ID[:\s]+(\d+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _decode_body(msg: email.message.Message) -> str:
    """Extract plain-text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _push_user(user: dict, text: str, configuration) -> None:
    """Send a push message to a user on their platform."""
    platform = user.get("platform", "line")
    uid = user["user_id"]
    if platform == "facebook":
        from facebook_messenger import fb_send
        fb_send(uid, text)
    else:
        with ApiClient(configuration) as client:
            MessagingApi(client).push_message(
                PushMessageRequest(to=uid, messages=[TextMessage(text=text)])
            )


def _notify_admin(configuration, message: str) -> None:
    admin_id = os.environ.get("ADMIN_LINE_USER_ID")
    if not admin_id:
        return
    try:
        with ApiClient(configuration) as client:
            MessagingApi(client).push_message(
                PushMessageRequest(to=admin_id, messages=[TextMessage(text=message)])
            )
    except Exception as e:
        logger.error(f"Admin notify failed: {e}")


def poll_new_iux_emails(configuration, db) -> None:
    """Poll Gmail for unread IUX referral emails and auto-verify matching users."""
    if not _is_enabled():
        return

    address = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]

    try:
        mail = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT)
        mail.login(address, app_password)
    except Exception as e:
        logger.error(f"Gmail IMAP login failed: {e}")
        return

    try:
        mail.select("INBOX")
        _, data = mail.search(None, f'(UNSEEN FROM "{_IUX_SENDER}")')
        email_ids = data[0].split() if data[0] else []

        if not email_ids:
            return

        logger.info(f"Found {len(email_ids)} unread IUX email(s)")

        for eid in email_ids:
            try:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                body = _decode_body(msg)
                iux_id = _extract_iux_id(body)

                if not iux_id:
                    logger.warning(f"No IUX User ID found in email {eid}")
                    mail.store(eid, "+FLAGS", "\\Seen")
                    continue

                logger.info(f"IUX email — User ID: {iux_id}")
                users = db.get_all_users_by_iux_id(iux_id)
                pending = [u for u in users if u.get("status") == "pending"]

                if not pending:
                    logger.info(f"IUX ID {iux_id} not found as pending — skipping")
                    mail.store(eid, "+FLAGS", "\\Seen")
                    continue

                for u in pending:
                    db.upsert_user(u["user_id"], platform=u["platform"], status="verified")
                    try:
                        _push_user(u, _VERIFY_MSG, configuration)
                    except Exception as e:
                        logger.error(f"Push failed for {u['user_id']}: {e}")

                platforms = ", ".join(u["platform"] for u in pending)
                _notify_admin(
                    configuration,
                    f"✅ Auto-verified IUX ID: {iux_id} ({platforms})\n"
                    f"จาก email IUX ที่เพิ่งเข้ามา",
                )
                logger.info(f"Auto-verified IUX ID: {iux_id} for {len(pending)} user(s)")

            except Exception as e:
                logger.error(f"Error processing email {eid}: {e}")
            finally:
                mail.store(eid, "+FLAGS", "\\Seen")

    finally:
        try:
            mail.logout()
        except Exception:
            pass
