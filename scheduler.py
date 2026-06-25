import logging
import os

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest, TextMessage

logger = logging.getLogger(__name__)

_LINE_ENABLED = os.environ.get("LINE_ENABLED", "true").lower() == "true"


def _line_push(configuration, user_id: str, text: str) -> None:
    """Push a message to an individual LINE user (used for reminders and admin notify)."""
    if not _LINE_ENABLED:
        return
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
        )


def send_morning_reminder(configuration, db) -> None:
    """Send a short nudge to the LINE group that signal is available for the day."""
    from line_notify import send_group_signal
    reminder = (
        "📊 Signal ทองคำประจำวันพร้อมแล้วครับ!\n\n"
        "👉 ส่ง /signal ในแชทบอทเพื่อดู signal ได้เลยครับ"
    )
    ok = send_group_signal(reminder)
    status = "✅ ส่งสำเร็จ" if ok else "⚠️ ไม่ได้ส่ง (LINE_BROADCAST_GROUP_ID ไม่ได้ตั้งค่า)"
    logger.info("Morning reminder: %s", status)
    _notify_admin(configuration, db, f"📢 ส่ง reminder เช้าเรียบร้อย\nLINE Group: {status}")


def broadcast_signal(configuration, db) -> None:
    """Generate and send the full signal to the LINE group (admin /broadcast command)."""
    from signal_gen import generate_gold_analysis

    try:
        logger.info("Generating daily signal...")
        signal = generate_gold_analysis()
    except Exception as e:
        logger.error(f"Signal generation failed: {e}")
        _notify_admin(configuration, db, f"❌ Generate signal ล้มเหลว: {e}")
        return

    from line_notify import send_group_signal
    ok = send_group_signal(signal)
    status = "✅ ส่งสำเร็จ" if ok else "⚠️ LINE_BROADCAST_GROUP_ID ไม่ได้ตั้งค่า — ไม่ได้ส่ง"
    logger.info("LINE group broadcast: %s", status)
    _notify_admin(configuration, db, f"📢 Broadcast เสร็จแล้ว\nLINE Group: {status}")


def send_pending_reminders(configuration, db) -> None:
    """Send a one-time reminder to users who have been pending for over 1 hours."""
    users = db.get_long_pending_users(hours=1)
    if not users:
        return

    logger.info(f"Sending pending reminders to {len(users)} user(s)")
    _REMINDER_MSG = (
        "⏳ Admin กำลังตรวจสอบบัญชี Exness ของคุณอยู่นะครับ\n\n"
        "หากยังไม่ได้สมัครผ่าน TradingTP สามารถสมัครได้ที่:\n"
        "https://one.exnessonelink.com/a/lut0605b6n\n\n"
        "หรือถ้ามีบัญชี Exness อยู่แล้ว ต้องโอนย้าย Partner ก่อนนะครับ\n"
        "→ ทัก Live Chat ที่เว็บ Exness (exness.com)\n"
        "→ Partner code: lut0605b6n\n\n"
        "หลังจากโอนย้ายเสร็จแล้วแจ้งผมได้เลยครับผม 🙏"
    )

    for user in users:
        uid = user["user_id"]
        platform = user.get("platform", "line")
        if platform == "line" and not _LINE_ENABLED:
            logger.debug(f"Skipping LINE reminder for {uid} (LINE_ENABLED=false)")
            continue
        try:
            if platform == "line":
                _line_push(configuration, uid, _REMINDER_MSG)
            elif platform == "facebook":
                from facebook_messenger import fb_send
                fb_send(uid, _REMINDER_MSG)
            db.upsert_user(uid, platform=platform, reminder_sent=True)
            logger.info(f"Reminder sent to {uid} ({platform})")
        except Exception as e:
            logger.error(f"Reminder failed for {uid}: {e}")


def _notify_admin(configuration, db, text: str) -> None:
    """Notify all admin users across all platforms."""
    if _LINE_ENABLED:
        admin_id = os.environ.get("ADMIN_LINE_USER_ID")
        if admin_id:
            try:
                _line_push(configuration, admin_id, text)
            except Exception as e:
                logger.error(f"Admin LINE notify failed: {e}")
    try:
        from facebook_messenger import fb_send as _fb_send
        for admin in db.get_admin_users():
            if admin.get("platform") == "facebook":
                try:
                    _fb_send(admin["user_id"], text)
                except Exception as e:
                    logger.error(
                        "FB admin notify failed uid=%s: %s", admin["user_id"], e)
    except Exception as e:
        logger.error("get_admin_users failed: %s", e)


def start_scheduler(configuration, db) -> BackgroundScheduler:
    _gmail_verify_enabled = os.environ.get("GMAIL_VERIFY_ENABLED", "true").lower() == "true"

    scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Bangkok"))

    if _gmail_verify_enabled:
        from gmail_poller import poll_new_iux_emails
        scheduler.add_job(
            poll_new_iux_emails,
            trigger="interval",
            minutes=60,
            args=[configuration, db],
            id="gmail_poll",
            name="Gmail Exness Auto-Verify",
            replace_existing=True,
        )
        logger.info("Gmail auto-verify job scheduled (every 60 min)")
    else:
        logger.info("Gmail auto-verify is DISABLED (GMAIL_VERIFY_ENABLED=false)")
    scheduler.add_job(
        send_pending_reminders,
        trigger="interval",
        minutes=70,
        args=[configuration, db],
        id="pending_reminder",
        name="Pending User Reminder",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — Gmail poll every 60 min, pending reminder every 70 min")
    return scheduler
