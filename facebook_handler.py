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

from facebook_messenger import fb_send, fb_send_recurring_opt_in, get_fb_profile

logger = logging.getLogger(__name__)

PLATFORM = "facebook"

_YES_WORDS = {"ใช่", "yes", "ใช่ครับ", "ใช่ค่ะ", "ถูก", "ถูกต้อง", "ok", "okay", "โอเค", "ใช่เลย"}
_NO_WORDS = {"ไม่", "no", "ไม่ใช่", "ผิด", "แก้", "แก้ไข", "เปลี่ยน"}

_VERIFY_MESSAGE = (
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_iux_id(text: str) -> str | None:
    matches = re.findall(r"\b(\d{6}|\d{8})\b", text)
    return matches[0] if matches else None


def _push_line_admin(text: str) -> None:
    """Push a notification to the LINE admin account."""
    admin_id = os.environ.get("ADMIN_LINE_USER_ID")
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not admin_id or not token:
        return
    config = Configuration(access_token=token)
    try:
        with ApiClient(config) as client:
            MessagingApi(client).push_message(
                PushMessageRequest(to=admin_id, messages=[TextMessage(text=text)])
            )
    except Exception as e:
        logger.error(f"Admin LINE push failed: {e}")


# ---------------------------------------------------------------------------
# Public event handlers (called from main.py)
# ---------------------------------------------------------------------------

def handle_fb_message(psid: str, text: str, db) -> None:
    """Route an incoming Messenger text message through the onboarding flow."""
    text = text.strip()
    user = db.get_user(psid, platform=PLATFORM)

    if not user:
        display_name = get_fb_profile(psid)
        db.upsert_user(
            psid, platform=PLATFORM,
            status="new", state="waiting_iux",
            display_name=display_name, pending_notified=False,
        )
        fb_send(
            psid,
            "สวัสดีครับ! ยินดีต้อนรับสู่ TradingTP 🎉\n\n"
            "เพื่อรับ Daily Trend ฟรีทุกเช้า 8:00 น. / Prompt หรือ โค้ดต่างๆ "
            "กรุณาส่ง IUX User ID ของคุณมาได้เลยครับ\n\n"
            "💡 IUX User ID คือตัวเลข 6 หรือ 8 หลักที่แสดงอยู่ในหน้า Profile ของ IUX ครับ\n\n"
            "หรือหากยังไม่มีบัญชี IUX สามารถสมัครฟรีได้ที่ "
            "https://iux.com/en/register?code=IuyjFrlz เลยครับ\n\n"
            "สำหรับคนที่มีบัญชี iux อยู่แล้ว ต้องโอนย้ายก่อนนะครับตามลิงค์นี้\n"
            "👇👇👇\n"
            "https://www.iux.com/en/dashboard/ib-transfers-request\n\n"
            "Partner referral code: IuyjFrlz\n\n"
            "หลังจากโอนย้ายเสร็จแล้วแจ้งผมได้เลยครับผม",
        )
        return

    state = user.get("state", "waiting_iux")
    if state == "waiting_iux":
        _handle_waiting_iux(psid, text, db)
    elif state == "confirming":
        _handle_confirming(psid, text, db, user)
    elif state == "done":
        _handle_done(psid, db, user)


def handle_fb_optin(psid: str, token: str, db) -> None:
    """Store the Recurring Notifications token when a user opts in."""
    db.upsert_user(psid, platform=PLATFORM, notification_token=token)
    logger.info(f"FB Recurring Notifications opt-in stored for {psid}")
    fb_send(psid, "✅ ขอบคุณครับ! คุณจะได้รับ Daily Signal ทุกเช้า 8:00 น. โดยอัตโนมัติ 📈")


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------

def _handle_waiting_iux(psid: str, text: str, db) -> None:
    iux_id = _extract_iux_id(text)
    if iux_id:
        db.upsert_user(psid, platform=PLATFORM, pending_iux_id=iux_id, state="confirming")
        fb_send(psid, f"IUX User ID: {iux_id} ใช่ไหมครับ? (พิมพ์ ใช่ หรือ ไม่)")


def _handle_confirming(psid: str, text: str, db, user: dict) -> None:
    if text.lower() in _YES_WORDS:
        pending = user.get("pending_iux_id")
        db.upsert_user(
            psid, platform=PLATFORM,
            iux_user_id=pending, pending_iux_id=None,
            status="pending", state="done", pending_notified=True,
        )
        fb_send(
            psid,
            f"✅ บันทึก IUX ID: {pending} เรียบร้อยครับ\n\n"
            "⏳ รอ Admin ยืนยันสักครู่นะครับ 🙏",
        )
        display_name = user.get("display_name") or get_fb_profile(psid) or psid
        _push_line_admin(
            f"🔔 มี User ใหม่รอยืนยัน! [Facebook]\n\n"
            f"ชื่อ Facebook: {display_name}\n"
            f"IUX User ID : {pending}\n\n"
            f"✅ ยืนยัน: /verify {pending}\n"
            f"❌ ปฏิเสธ: /reject {pending}",
        )

    elif text.lower() in _NO_WORDS:
        db.upsert_user(psid, platform=PLATFORM, pending_iux_id=None, state="waiting_iux")
        fb_send(psid, "โอเคครับ กรุณาส่ง IUX User ID ใหม่ได้เลยครับ 😊")

    else:
        fb_send(psid, "กรุณาตอบ 'ใช่' หรือ 'ไม่' ครับ")


def _handle_done(psid: str, db, user: dict) -> None:
    status = user.get("status")
    if status == "pending":
        if not user.get("pending_notified"):
            fb_send(
                psid,
                "⏳ กำลังรอ Admin ยืนยัน IUX User ID ของคุณอยู่ครับ\n"
                "จะแจ้งให้ทราบเมื่อผ่านแล้ว 🙏",
            )
            db.upsert_user(psid, platform=PLATFORM, pending_notified=True)
        return

    if status == "verified":
        # ส่ง opt-in button อีกครั้งในกรณีที่ยังไม่มี token
        if not user.get("notification_token"):
            try:
                fb_send_recurring_opt_in(psid)
            except Exception as e:
                logger.warning(f"Could not send recurring opt-in to {psid}: {e}")
        return

    if status == "rejected":
        db.upsert_user(
            psid, platform=PLATFORM,
            status="new", state="waiting_iux",
            iux_user_id=None, pending_iux_id=None, pending_notified=False,
        )
        fb_send(
            psid,
            "❌ IUX User ID ไม่ผ่านการยืนยันครับ\n\n"
            "กรุณาส่ง IUX User ID ใหม่ได้เลยครับ\n"
            "(ตรวจสอบว่าสมัคร IUX ผ่าน affiliate link ของ TradingTP แล้วนะครับ)",
        )


# ---------------------------------------------------------------------------
# Verification message (shared with main.py admin /verify command)
# ---------------------------------------------------------------------------

def get_verify_message() -> str:
    return _VERIFY_MESSAGE
