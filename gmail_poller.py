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
    "📊 วิธีดู Daily Signal:\n"
    "พิมพ์ /signal ในแชทบอทเพื่อดู signal ทองคำประจำวัน (วันละ 3 ครั้ง)\n\n"
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

    # MANUAL_ entries are placeholders with no real messaging channel
    if uid.startswith("MANUAL_"):
        logger.info("Skipping push for MANUAL user %s — no real messaging channel", uid)
        return

    if platform == "facebook":
        from facebook_messenger import fb_send
        fb_send(uid, text)
    else:
        line_enabled = os.environ.get("LINE_ENABLED", "true").lower() == "true"
        if not line_enabled:
            logger.info("LINE disabled — skipping push for LINE user %s", uid)
            return
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


def _poll_iux_emails(configuration, db, unseen_only: bool) -> list[dict]:
    """Core polling logic. If unseen_only=True, fetch only unread emails and mark them read."""
    if not _is_enabled():
        return []

    address = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]

    try:
        mail = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT)
        mail.login(address, app_password)
    except Exception as e:
        logger.error(f"Gmail IMAP login failed: {e}")
        return []

    verified_results: list[dict] = []

    try:
        mail.select("INBOX")
        criteria = f'(UNSEEN FROM "{_IUX_SENDER}")' if unseen_only else f'FROM "{_IUX_SENDER}"'
        _, data = mail.search(None, criteria)
        email_ids = data[0].split() if data[0] else []

        if not email_ids:
            return []

        logger.info(f"Found {len(email_ids)} IUX email(s) (unseen_only={unseen_only})")

        for eid in email_ids:
            try:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                body = _decode_body(msg)
                iux_id = _extract_iux_id(body)

                if not iux_id:
                    logger.warning(f"No IUX User ID found in email {eid}")
                    if unseen_only:
                        mail.store(eid, "+FLAGS", "\\Seen")
                    continue

                logger.info(f"IUX email — User ID: {iux_id}")
                users = db.get_all_users_by_iux_id(iux_id)
                pending = [u for u in users if u.get("status") == "pending"]

                if not pending:
                    logger.info(f"IUX ID {iux_id} not found as pending — skipping")
                    # Do NOT mark as Seen — user may register later and we need to re-process
                    continue

                for u in pending:
                    db.upsert_user(u["user_id"], platform=u["platform"], status="verified")
                    try:
                        _push_user(u, _VERIFY_MSG, configuration)
                    except Exception as e:
                        logger.error(f"Push failed for {u['user_id']}: {e}")
                    if u.get("platform") == "facebook" and not u.get("notification_token"):
                        try:
                            from facebook_messenger import fb_send_recurring_opt_in
                            fb_send_recurring_opt_in(u["user_id"])
                        except Exception as e:
                            logger.warning("Could not send recurring opt-in to %s: %s", u["user_id"], e)
                    verified_results.append({
                        "iux_id": iux_id,
                        "display_name": u.get("display_name") or "-",
                        "platform": u.get("platform", "line"),
                    })

                # Mark as Seen only after successfully verifying at least one user
                if unseen_only:
                    mail.store(eid, "+FLAGS", "\\Seen")
                logger.info(f"Auto-verified IUX ID: {iux_id} for {len(pending)} user(s)")

            except Exception as e:
                logger.error(f"Error processing email {eid}: {e}")

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return verified_results


def poll_new_iux_emails(configuration, db) -> list[dict]:
    """Scheduled job: check only UNSEEN emails, mark as read after processing."""
    return _poll_iux_emails(configuration, db, unseen_only=True)


def poll_all_iux_emails(configuration, db) -> list[dict]:
    """Manual trigger (/autoverifynow): scan ALL emails from IUX regardless of read status."""
    return _poll_iux_emails(configuration, db, unseen_only=False)
