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

from facebook_messenger import fb_send, fb_send_recurring_opt_in, get_fb_profile, take_thread_control

logger = logging.getLogger(__name__)

PLATFORM = "facebook"

_YES_WORDS = {"ใช่", "yes", "ใช่ครับ", "ใช่ค่ะ",
              "ถูก", "ถูกต้อง", "ok", "okay", "โอเค", "ใช่เลย"}
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


def _line_config() -> Configuration:
    return Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])


def _push_line_admin(text: str) -> None:
    """Push a notification to the LINE admin account."""
    admin_id = os.environ.get("ADMIN_LINE_USER_ID")
    if not admin_id:
        return
    try:
        with ApiClient(_line_config()) as client:
            MessagingApi(client).push_message(
                PushMessageRequest(to=admin_id, messages=[
                                   TextMessage(text=text)])
            )
    except Exception as e:
        logger.error(f"Admin LINE push failed: {e}")


def _notify_all_admins(text: str, db=None) -> None:
    """Notify every admin user across all platforms."""
    _push_line_admin(text)
    if db is None:
        return
    try:
        for admin in db.get_admin_users():
            if admin.get("platform") == "facebook":
                try:
                    fb_send(admin["user_id"], text)
                except Exception as e:
                    logger.error("FB admin notify failed uid=%s: %s", admin["user_id"], e)
    except Exception as e:
        logger.error("get_admin_users failed: %s", e)


def _push_line_user(user_id: str, text: str) -> None:
    """Push a message to a LINE user."""
    try:
        with ApiClient(_line_config()) as client:
            MessagingApi(client).push_message(
                PushMessageRequest(to=user_id, messages=[
                                   TextMessage(text=text)])
            )
    except Exception as e:
        logger.error(f"LINE push failed for {user_id}: {e}")


def _push_to_user(user: dict, text: str) -> None:
    """Send a message to a user on their platform."""
    if user.get("platform") == "facebook":
        fb_send(user["user_id"], text)
    else:
        _push_line_user(user["user_id"], text)


# ---------------------------------------------------------------------------
# Public event handlers (called from main.py)
# ---------------------------------------------------------------------------

def handle_fb_message(psid: str, text: str, db, configuration=None) -> None:
    """Route an incoming Messenger text message."""
    # Reclaim thread from inbox/secondary receiver when our app is Primary Receiver
    take_thread_control(psid)

    text = text.strip()
    user = db.get_user(psid, platform=PLATFORM)

    # Admin check — route before normal flow
    if user and user.get("user_role") == "admin":
        _handle_fb_admin(psid, text, db, configuration)
        return

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

    # Backfill display_name if missing (e.g. user joined while app was in Dev Mode)
    if not user.get("display_name"):
        fetched_name = get_fb_profile(psid)
        if fetched_name:
            db.upsert_user(psid, platform=PLATFORM, display_name=fetched_name)
            user["display_name"] = fetched_name
            logger.info("Backfilled display_name for psid=%s: %s",
                        psid, fetched_name)

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
    fb_send(
        psid, "✅ ขอบคุณครับ! คุณจะได้รับ Daily Signal ทุกเช้า 8:00 น. โดยอัตโนมัติ 📈")


# ---------------------------------------------------------------------------
# Normal user state handlers
# ---------------------------------------------------------------------------

def _handle_waiting_iux(psid: str, text: str, db) -> None:
    iux_id = _extract_iux_id(text)
    if iux_id:
        db.upsert_user(psid, platform=PLATFORM,
                       pending_iux_id=iux_id, state="confirming")
        fb_send(
            psid, f"IUX User ID: {iux_id} ใช่ไหมครับ? (พิมพ์ ใช่ หรือ ไม่)")


def _handle_confirming(psid: str, text: str, db, user: dict) -> None:
    if text.lower() in _YES_WORDS:
        pending = user.get("pending_iux_id")

        # ถ้า IUX ID นี้ verified บน platform อื่นอยู่แล้ว → verify ทันทีไม่ต้องรอ email
        existing = db.get_all_users_by_iux_id(pending)
        already_verified = any(
            u.get("status") == "verified" and u.get("user_id") != psid
            for u in existing
        )

        if already_verified:
            db.upsert_user(
                psid, platform=PLATFORM,
                iux_user_id=pending, pending_iux_id=None,
                status="verified", state="done", pending_notified=True,
            )
            fb_send(psid, _VERIFY_MESSAGE)
        else:
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
            display_name = user.get(
                "display_name") or get_fb_profile(psid) or psid
            _notify_all_admins(
                f"🔔 มี User ใหม่รอยืนยัน! [Facebook]\n\n"
                f"ชื่อ Facebook: {display_name}\n"
                f"IUX User ID : {pending}\n\n"
                f"✅ ยืนยัน: /verify {pending}\n"
                f"❌ ปฏิเสธ: /reject {pending}",
                db=db,
            )

    elif text.lower() in _NO_WORDS:
        db.upsert_user(psid, platform=PLATFORM,
                       pending_iux_id=None, state="waiting_iux")
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
        if not user.get("notification_token"):
            try:
                fb_send_recurring_opt_in(psid)
            except Exception as e:
                logger.warning(
                    f"Could not send recurring opt-in to {psid}: {e}")
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
# Facebook admin command handler
# ---------------------------------------------------------------------------

def _handle_fb_admin(psid: str, text: str, db, configuration) -> None:
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    def send(msg: str) -> None:
        fb_send(psid, msg)

    if cmd == "/addpending" and arg:
        existing = db.get_user_by_iux_id(arg)
        if existing:
            send(
                f"⚠️ IUX ID: {arg} มีในระบบแล้ว (status: {existing.get('status')})")
        else:
            fake_id = f"MANUAL_{arg}"
            db.upsert_user(fake_id, iux_user_id=arg, status="pending",
                           state="done", display_name=f"[Manual] {arg}")
            send(
                f"✅ เพิ่ม IUX ID: {arg} เข้าระบบแล้ว\nใช้ /verify {arg} เพื่อยืนยันได้เลย")

    elif cmd in ("/verify", "/vertify") and arg:
        users = db.get_all_users_by_iux_id(arg)
        if users:
            for u in users:
                db.upsert_user(
                    u["user_id"], platform=u["platform"], status="verified")
                _push_to_user(u, _VERIFY_MESSAGE)
            platforms = ", ".join(u["platform"] for u in users)
            send(f"✅ Verified IUX ID: {arg} ({platforms})")
        else:
            send(f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/reject" and arg:
        users = db.get_all_users_by_iux_id(arg)
        if users:
            for u in users:
                db.upsert_user(
                    u["user_id"], platform=u["platform"], status="rejected")
                _push_to_user(
                    u,
                    "❌ IUX User ID ไม่ผ่านการยืนยันครับ\n\n"
                    "กรุณาตรวจสอบว่าสมัคร IUX ผ่าน affiliate link ของ TradingTP\n"
                    "แล้วส่ง ID มาใหม่ได้เลยครับ 🙏",
                )
            send(f"❌ Rejected IUX ID: {arg}")
        else:
            send(f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/update" and arg:
        parts_arg = arg.split()
        if len(parts_arg) != 2:
            send(
                "❌ รูปแบบไม่ถูกต้อง\nใช้: /update [iux_id_เก่า] [iux_id_ใหม่]")
            return
        old_id, new_id = parts_arg
        user = db.get_user_by_iux_id(old_id)
        if user:
            db.update_iux_id(user["user_id"], new_id,
                             platform=user.get("platform", "line"))
            send(f"✅ อัปเดต IUX ID เรียบร้อย\nเก่า: {old_id}\nใหม่: {new_id}")
        else:
            send(f"❌ ไม่พบ IUX ID: {old_id} ในระบบ")

    elif cmd == "/findname" and arg:
        users = db.search_by_name(arg)
        if users:
            lines = []
            for u in users:
                lines.append(
                    f"👤 {u.get('display_name', '?')}\n"
                    f"   IUX: {u.get('iux_user_id', '-')}\n"
                    f"   Platform: {u.get('platform', '-')}\n"
                    f"   Status: {u.get('status', '-')}"
                )
            send(f"🔍 ค้นหา '{arg}' พบ {len(users)} คน\n\n" +
                 "\n\n".join(lines))
        else:
            send(f"❌ ไม่พบชื่อที่ค้นหา: '{arg}'")

    elif cmd == "/info" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            verified_at = user.get("verified_at", "-") or "-"
            created_at = user.get("created_at", "-") or "-"
            send(
                f"📋 ข้อมูล User\n\n"
                f"ชื่อ         : {user.get('display_name', '-')}\n"
                f"IUX User ID  : {user.get('iux_user_id', '-')}\n"
                f"Platform     : {user.get('platform', '-')}\n"
                f"Status       : {user.get('status', '-')}\n"
                f"สมัครวันที่  : {created_at[:10] if len(created_at) > 10 else created_at}\n"
                f"Verify วันที่: {verified_at[:10] if len(verified_at) > 10 else verified_at}"
            )
        else:
            send(f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/reset" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            db.reset_user(user["user_id"],
                          platform=user.get("platform", "line"))
            send(f"🔄 Reset user IUX ID: {arg} แล้ว")
        else:
            send(f"❌ ไม่พบ IUX ID: {arg}")

    elif cmd == "/list":
        users = db.get_all_users()
        verified = [u for u in users if u["status"] == "verified"]
        pending = [u for u in users if u["status"] == "pending"]
        line_v = sum(1 for u in verified if u.get("platform") == "line")
        fb_v = sum(1 for u in verified if u.get("platform") == "facebook")
        pending_str = "\n".join(
            f"  • {u['iux_user_id']} [{u.get('platform', 'line')}]" for u in pending
        ) or "  (ไม่มี)"
        send(
            f"📊 สรุป Users ทั้งหมด\n\n"
            f"✅ Verified: {len(verified)} คน (LINE: {line_v}, FB: {fb_v})\n"
            f"⏳ Pending: {len(pending)} คน\n"
            f"👥 Total: {len(users)} คน\n\n"
            f"Pending IDs:\n{pending_str}"
        )

    elif cmd == "/autoverifynow":
        send("⏳ กำลังเช็ค email จาก IUX ทันที...")
        from gmail_poller import poll_all_iux_emails
        try:
            verified = poll_all_iux_emails(configuration, db)
            if verified:
                lines = "\n".join(
                    f"{i+1}. IUX ID: {v['iux_id']}  ชื่อ {v['platform'].upper()}: {v['display_name']}"
                    for i, v in enumerate(verified)
                )
                send(
                    f"✅ เช็ค email เสร็จแล้ว\n\nUser ใหม่ที่ verify แล้ว:\n{lines}")
            else:
                send("✅ เช็ค email เสร็จแล้ว\nไม่พบ user ใหม่ที่รอ verify")
        except Exception as e:
            send(f"❌ Auto-verify ล้มเหลว: {e}")

    elif cmd == "/broadcast":
        send("⏳ กำลัง broadcast signal ไปหา verified users...")
        from scheduler import broadcast_signal
        broadcast_signal(configuration, db)

    elif cmd == "/dailycheck":
        send("⏳ กำลังวิเคราะห์ทองคำ รอแป๊บนึงครับ...")
        from signal_gen import generate_gold_analysis
        try:
            analysis = generate_gold_analysis()
            fb_send(psid, analysis)
        except Exception as e:
            send(f"❌ วิเคราะห์ทองไม่สำเร็จ: {e}")

    elif cmd == "/signal":
        send("⏳ กำลัง generate signal... รอแป๊บนึงครับ")
        from signal_gen import generate_signal
        try:
            signal = generate_signal()
            fb_send(psid, signal)
        except Exception as e:
            send(f"❌ Generate signal ล้มเหลว: {e}")

    elif cmd == "/help":
        send(
            "📋 Admin Commands:\n\n"
            "/addpending [ID]      — เพิ่ม IUX ID เข้าระบบ (manual)\n"
            "/verify [ID]          — ยืนยัน user (ทุก platform)\n"
            "/reject [ID]          — ปฏิเสธ user (ทุก platform)\n"
            "/update [เก่า] [ใหม่]  — แก้ IUX ID ของ user\n"
            "/reset [ID]           — reset ให้ user ส่ง ID ใหม่\n"
            "/info [ID]            — ดูข้อมูล user\n"
            "/findname [ชื่อ]       — ค้นหา user จากชื่อ\n"
            "/list                 — ดู users ทั้งหมด\n"
            "/signal              — generate signal ให้ตัวเอง\n"
            "/dailycheck          — วิเคราะห์ทองคำทันที\n"
            "/broadcast           — broadcast ไปหา verified users\n"
            "/autoverifynow       — เช็ค email IUX และ verify ทันที\n"
            "/help                — แสดง commands"
        )

    else:
        send("❓ ไม่รู้จัก command นี้ครับ\nพิมพ์ /help เพื่อดูคำสั่งทั้งหมด")


# ---------------------------------------------------------------------------
# Verification message accessor
# ---------------------------------------------------------------------------

def get_verify_message() -> str:
    return _VERIFY_MESSAGE
