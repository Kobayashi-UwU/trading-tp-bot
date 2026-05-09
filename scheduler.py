import logging
import os

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest, TextMessage

logger = logging.getLogger(__name__)


def broadcast_signal(configuration, db) -> None:
    from signal_gen import generate_signal

    try:
        logger.info("Generating daily signal...")
        signal = generate_signal()
    except Exception as e:
        logger.error(f"Signal generation failed: {e}")
        _notify_admin(configuration, db, f"❌ Generate signal ล้มเหลว: {e}")
        return

    verified_users = db.get_verified_users()
    logger.info(f"Broadcasting to {len(verified_users)} verified users")

    success, failed = 0, 0
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        for user in verified_users:
            try:
                api.push_message(
                    PushMessageRequest(
                        to=user["line_user_id"],
                        messages=[TextMessage(text=signal)],
                    )
                )
                success += 1
            except Exception as e:
                logger.error(f"Push failed for {user['line_user_id']}: {e}")
                failed += 1

    logger.info(f"Broadcast done — success: {success}, failed: {failed}")
    _notify_admin(
        configuration,
        db,
        f"✅ Broadcast เสร็จแล้ว\nส่งสำเร็จ: {success} คน\nล้มเหลว: {failed} คน",
    )


def _notify_admin(configuration, db, message: str) -> None:
    admin_id = os.environ.get("ADMIN_LINE_USER_ID")
    if not admin_id:
        return
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=admin_id,
                    messages=[TextMessage(text=message)],
                )
            )
    except Exception as e:
        logger.error(f"Admin notify failed: {e}")


def start_scheduler(configuration, db) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Bangkok"))
    scheduler.add_job(
        broadcast_signal,
        trigger=CronTrigger(hour=8, minute=0, timezone="Asia/Bangkok"),
        args=[configuration, db],
        id="daily_signal",
        name="Daily Morning Signal",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — Daily signal at 08:00 Bangkok time")
    return scheduler
