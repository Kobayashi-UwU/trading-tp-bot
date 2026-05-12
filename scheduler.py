import logging
import os

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest, TextMessage

logger = logging.getLogger(__name__)


def broadcast_signal(configuration, db) -> None:
    from signal_gen import generate_gold_analysis

    try:
        logger.info("Generating daily signal...")
        signal = generate_gold_analysis()
    except Exception as e:
        logger.error(f"Signal generation failed: {e}")
        _notify_admin(configuration, f"❌ Generate signal ล้มเหลว: {e}")
        return

    verified_users = db.get_verified_users()
    logger.info(f"Broadcasting to {len(verified_users)} verified users")

    success, failed = 0, 0
    with ApiClient(configuration) as api_client:
        line_api = MessagingApi(api_client)
        for user in verified_users:
            uid = user["user_id"]
            platform = user.get("platform", "line")
            try:
                if platform == "line":
                    line_api.push_message(
                        PushMessageRequest(to=uid, messages=[
                                           TextMessage(text=signal)])
                    )
                elif platform == "facebook":
                    from facebook_messenger import fb_push
                    fb_push(uid, signal, user.get("notification_token"))
                success += 1
            except Exception as e:
                logger.error(f"Push failed for {uid} ({platform}): {e}")
                failed += 1

    logger.info(f"Broadcast done — success: {success}, failed: {failed}")
    _notify_admin(
        configuration,
        f"✅ Broadcast เสร็จแล้ว\nส่งสำเร็จ: {success} คน\nล้มเหลว: {failed} คน",
    )


def send_pending_reminders(configuration, db) -> None:
    """Send a one-time reminder to users who have been pending for over 12 hours."""
    users = db.get_long_pending_users(hours=12)
    if not users:
        return

    logger.info(f"Sending pending reminders to {len(users)} user(s)")
    _REMINDER_MSG = (
        "ชื่อยังไม่ขึ้นในระบบนะครับ แนะนำให้ลองทำการโอนย้ายก่อนตามลิงค์นี้\n"
        "👇👇👇\n"
        "https://www.iux.com/en/dashboard/ib-transfers-request\n\n"
        "Partner referral code:\n"
        "IuyjFrlz\n\n"
        "หลังจากโอนย้ายเสร็จแล้วแจ้งผมได้เลยครับผม"
    )

    with ApiClient(configuration) as api_client:
        line_api = MessagingApi(api_client)
        for user in users:
            uid = user["user_id"]
            platform = user.get("platform", "line")
            try:
                if platform == "line":
                    line_api.push_message(
                        PushMessageRequest(to=uid, messages=[
                                           TextMessage(text=_REMINDER_MSG)])
                    )
                elif platform == "facebook":
                    from facebook_messenger import fb_send
                    fb_send(uid, _REMINDER_MSG)
                db.upsert_user(uid, platform=platform, reminder_sent=True)
                logger.info(f"Reminder sent to {uid} ({platform})")
            except Exception as e:
                logger.error(f"Reminder failed for {uid}: {e}")

    admin_id = os.environ.get("ADMIN_LINE_USER_ID")
    if not admin_id:
        return
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(to=admin_id, messages=[
                                   TextMessage(text=message)])
            )
    except Exception as e:
        logger.error(f"Admin notify failed: {e}")


def start_scheduler(configuration, db) -> BackgroundScheduler:
    from gmail_poller import poll_new_iux_emails

    scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Bangkok"))
    scheduler.add_job(
        broadcast_signal,
        trigger=CronTrigger(
            hour=8, minute=0, day_of_week="mon-fri", timezone="Asia/Bangkok"),
        args=[configuration, db],
        id="daily_signal",
        name="Daily Morning Signal",
        replace_existing=True,
    )
    scheduler.add_job(
        poll_new_iux_emails,
        trigger="interval",
        minutes=10,
        args=[configuration, db],
        id="gmail_poll",
        name="Gmail IUX Auto-Verify",
        replace_existing=True,
    )
    scheduler.add_job(
        send_pending_reminders,
        trigger="interval",
        minutes=30,
        args=[configuration, db],
        id="pending_reminder",
        name="Pending User Reminder",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — Daily signal at 08:00 Bangkok time, Gmail poll every 10 min")
    return scheduler
