import logging
import os
import re
import threading

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    FollowEvent,
    JoinEvent,
    MessageEvent,
    TextMessageContent,
    UnfollowEvent,
)

import facebook_handler
from db import Database
from facebook_messenger import fb_send, verify_fb_signature
from scheduler import start_scheduler

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_LINE_USER_ID = os.environ["ADMIN_LINE_USER_ID"]
LINE_ENABLED = os.environ.get("LINE_ENABLED", "true").lower() == "true"

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
db = Database()


# ---------------------------------------------------------------------------
# LINE helpers
# ---------------------------------------------------------------------------

def messaging_api() -> MessagingApi:
    return MessagingApi(ApiClient(configuration))


def reply(reply_token: str, text: str) -> None:
    if not LINE_ENABLED:
        return
    messaging_api().reply_message(
        ReplyMessageRequest(reply_token=reply_token,
                            messages=[TextMessage(text=text)])
    )


def push(user_id: str, text: str) -> None:
    if not LINE_ENABLED:
        return
    messaging_api().push_message(
        PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
    )


def push_to_user(user: dict, text: str) -> None:
    """Send a push message to a user on their platform (LINE or Facebook)."""
    platform = user.get("platform", "line")
    uid = user["user_id"]
    if platform == "facebook":
        fb_send(uid, text)
    else:
        push(uid, text)


def extract_iux_id(text: str) -> str | None:
    """ดึงตัวเลข 5, 6 หรือ 8 หลักจากข้อความ"""
    matches = re.findall(r"\b(\d{8}|\d{6}|\d{5})\b", text)
    return matches[0] if matches else None


def get_display_name(user_id: str) -> str:
    try:
        profile = messaging_api().get_profile(user_id)
        return profile.display_name
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# LINE webhook
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ---------------------------------------------------------------------------
# Facebook Messenger webhook
# ---------------------------------------------------------------------------

@app.route("/webhook/facebook", methods=["GET"])
def fb_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == os.environ.get("FB_VERIFY_TOKEN"):
        logger.info("Facebook webhook verified")
        return challenge, 200
    abort(403)


@app.route("/webhook/facebook", methods=["POST"])
def fb_webhook():
    body = request.get_data()
    logger.info("FB webhook POST received, body_len=%d", len(body))

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_fb_signature(body, signature):
        logger.warning("FB webhook signature mismatch — rejected")
        abort(400)

    data = request.get_json(silent=True) or {}
    logger.info("FB webhook payload object=%s entries=%d",
                data.get("object"), len(data.get("entry", [])))

    if data.get("object") != "page":
        return "OK"

    # Collect events to process, then return 200 immediately.
    # Facebook retries if it doesn't receive 200 within 5 seconds.
    events_to_process = []
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            psid = event.get("sender", {}).get("id")
            if not psid:
                continue
            logger.info("FB event psid=%s keys=%s", psid, list(event.keys()))
            events_to_process.append(("messaging", psid, event))

        # Standby events fire when the bot is in standby mode (e.g. after an
        # admin sends a message via Business Suite / Inbox).  We reclaim thread
        # control so subsequent user messages are routed back to the bot.
        for event in entry.get("standby", []):
            psid = event.get("sender", {}).get("id")
            if not psid:
                continue
            logger.info("FB standby event psid=%s keys=%s",
                        psid, list(event.keys()))
            events_to_process.append(("standby", psid, event))

    if events_to_process:
        threading.Thread(
            target=_process_fb_events,
            args=(events_to_process,),
            daemon=True,
        ).start()

    return "OK"


def _process_fb_events(events: list) -> None:
    """Process Facebook webhook events in a background thread."""
    for event_type, psid, event in events:
        try:
            if event_type == "standby":
                # Bot is in standby – reclaim the thread so the user can be
                # served.  Process the message normally; handle_fb_message
                # calls take_thread_control as its first action.
                text = event.get("message", {}).get("text", "").strip()
                logger.info(
                    "FB standby message psid=%s text=%r — reclaiming thread", psid, text)
                if text:
                    facebook_handler.handle_fb_message(
                        psid, text, db, configuration)
                else:
                    from facebook_messenger import take_thread_control
                    take_thread_control(psid)
                continue

            if "message" in event and not event["message"].get("is_echo"):
                text = event["message"].get("text", "").strip()
                logger.info("FB message psid=%s text=%r", psid, text)
                if text:
                    facebook_handler.handle_fb_message(
                        psid, text, db, configuration)

            elif "pass_thread_control" in event:
                new_owner = event["pass_thread_control"].get(
                    "new_thread_owner_app_id", "")
                logger.info(
                    "FB pass_thread_control psid=%s new_owner=%s", psid, new_owner)

            elif "take_thread_control" in event:
                prev_owner = event["take_thread_control"].get(
                    "previous_thread_owner_app_id", "")
                logger.info(
                    "FB take_thread_control psid=%s prev_owner=%s", psid, prev_owner)

            elif "request_thread_control" in event:
                logger.info(
                    "FB request_thread_control psid=%s — ignoring", psid)

            elif "optin" in event:
                optin = event["optin"]
                if optin.get("type") == "notification_messages":
                    token = optin.get("notification_messages_token", "")
                    facebook_handler.handle_fb_optin(psid, token, db)

        except Exception as e:
            logger.error("Error processing FB event psid=%s: %s", psid, e)


_scheduler = None


@app.route("/health", methods=["GET"])
def health():
    status = {"status": "ok", "scheduler": "unknown"}
    if _scheduler is not None:
        status["scheduler"] = "running" if _scheduler.running else "stopped"
    if status["scheduler"] == "stopped":
        return jsonify(status), 500
    return jsonify(status)


@app.route("/privacy-policy", methods=["GET"])
def privacy_policy():
    """Privacy policy for Trading TP Bot."""
    return """
    <html><head><title>Privacy Policy - Trading TP Bot</title></head>
    <body style="font-family:sans-serif;max-width:800px;margin:40px auto;padding:0 20px">
    <h1>Privacy Policy</h1>
    <p><strong>Trading TP Bot</strong> ("the Bot") is a Messenger chatbot that provides
    forex trading signals and manages user subscriptions.</p>
    <h2>Data We Collect</h2>
    <ul>
      <li>Your Messenger Page-Scoped ID (PSID) to identify you</li>
      <li>Your display name (fetched from Messenger profile)</li>
      <li>Your IUX broker user ID (submitted by you for verification)</li>
      <li>Subscription and verification status</li>
    </ul>
    <h2>How We Use Your Data</h2>
    <p>Data is used solely to deliver trading signals and manage your subscription.
    We do not sell or share your data with third parties.</p>
    <h2>Data Retention</h2>
    <p>Your data is retained while you are an active subscriber. You may request
    deletion at any time by messaging the bot with <code>/delete</code>.</p>
    <h2>Contact</h2>
    <p>For questions, contact: tai-k2003@hotmail.com</p>
    <p><em>Last updated: May 2026</em></p>
    </body></html>
    """, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/setup/facebook", methods=["GET"])
def setup_facebook():
    """Check Facebook page connection and webhook subscription status"""
    token = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
    app_id = os.environ.get("FB_APP_ID", "")
    if not token:
        return {"error": "FB_PAGE_ACCESS_TOKEN not set"}, 400

    import requests as req
    r = req.get(
        "https://graph.facebook.com/v20.0/me",
        params={"fields": "id,name", "access_token": token},
        timeout=10,
    )
    return {"page": r.json(), "fb_app_id_configured": bool(app_id)}


# ---------------------------------------------------------------------------
# LINE event: Follow
# ---------------------------------------------------------------------------

@handler.add(FollowEvent)
def handle_follow(event):
    if not LINE_ENABLED:
        return
    user_id = event.source.user_id
    display_name = get_display_name(user_id)
    db.upsert_user(user_id, status="new", state="waiting_iux",
                   display_name=display_name, pending_notified=False)
    reply(
        event.reply_token,
        "สวัสดีครับ! ยินดีต้อนรับสู่ TradingTP 🎉\n\n"
        "เพื่อรับ Daily Signal ฟรี / Prompt หรือ โค้ดต่างๆ กรุณาส่ง IUX User ID ของคุณมาได้เลยครับ\n\n"
        "💡 IUX User ID คือตัวเลข 5, 6 หรือ 8 หลักที่แสดงอยู่ในหน้า Profile ของ IUX ครับ\n\n"
        "หรือหากยังไม่มีบัญชี IUX สามารถสมัครฟรีได้ที่ https://iux.com/en/register?code=IuyjFrlz เลยครับ\n\n"
        "สำหรับคนที่มีบัญชี iux อยู่แล้ว ต้องโอนย้ายก่อนนะครับตามลิงค์นี้\n"
        "👇👇👇\n"
        "https://www.iux.com/en/dashboard/ib-transfers-request\n\n"
        "Partner referral code: IuyjFrlz\n\n"
        "หลังจากโอนย้ายเสร็จแล้วแจ้งผมได้เลยครับผม",
    )


# ---------------------------------------------------------------------------
# LINE event: Unfollow
# ---------------------------------------------------------------------------

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    db.upsert_user(event.source.user_id, status="blocked")


# ---------------------------------------------------------------------------
# LINE event: Join group — capture group ID and notify admin
# ---------------------------------------------------------------------------

@handler.add(JoinEvent)
def handle_join(event):
    """Fired when the bot is added to a LINE group or room."""
    source = event.source
    group_id = getattr(source, "group_id", None) or getattr(
        source, "room_id", None)
    if not group_id:
        logger.warning("JoinEvent fired but could not extract group/room ID")
        return

    logger.info("Bot joined LINE group/room: %s", group_id)

    msg = (
        f"🎉 Bot ถูกเพิ่มเข้ากลุ่ม LINE แล้ว!\n\n"
        f"Group ID: {group_id}\n\n"
        f"คัดลอก ID นี้ไปตั้งค่า LINE_BROADCAST_GROUP_ID ใน Railway ด้วยครับ"
    )

    # Notify Facebook admin
    try:
        from facebook_messenger import fb_send as _fb_send
        for admin in db.get_admin_users():
            if admin.get("platform") == "facebook":
                try:
                    _fb_send(admin["user_id"], msg)
                except Exception as _e:
                    logger.error("FB admin notify failed: %s", _e)
    except Exception as _e:
        logger.error("get_admin_users failed: %s", _e)

    # Also notify LINE admin if enabled
    if LINE_ENABLED:
        push(ADMIN_LINE_USER_ID, msg)


# ---------------------------------------------------------------------------
# LINE event: Message
# ---------------------------------------------------------------------------

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    if not LINE_ENABLED:
        return
    user_id = event.source.user_id
    text = event.message.text.strip()
    reply_token = event.reply_token

    if user_id == ADMIN_LINE_USER_ID:
        _handle_admin(text, reply_token)
        return

    user = db.get_user(user_id)
    if not user:
        db.upsert_user(user_id, status="new", state="waiting_iux")
        reply(reply_token, "กรุณาส่ง IUX User ID ของคุณเพื่อรับสิทธิ์ Daily Signal / Code / Prompt ครับ")
        return

    if text.lower() == "/signal" and user.get("status") == "verified":
        _handle_user_signal(user, reply_token)
        return

    state = user.get("state", "waiting_iux")
    if state == "waiting_iux":
        _handle_waiting_iux(user_id, text, reply_token)
    elif state == "confirming":
        _handle_confirming(user_id, text, reply_token, user)
    elif state == "done":
        _handle_done(user, reply_token)


# ---------------------------------------------------------------------------
# LINE state handlers
# ---------------------------------------------------------------------------

def _handle_waiting_iux(user_id: str, text: str, reply_token: str) -> None:
    iux_id = extract_iux_id(text)
    if iux_id:
        db.upsert_user(user_id, pending_iux_id=iux_id, state="confirming")
        reply(reply_token,
              f"IUX User ID: {iux_id} ใช่ไหมครับ? (พิมพ์ ใช่ หรือ ไม่)")


def _handle_confirming(user_id: str, text: str, reply_token: str, user: dict) -> None:
    yes_words = {"ใช่", "yes", "ใช่ครับ", "ใช่ค่ะ",
                 "ถูก", "ถูกต้อง", "ok", "okay", "โอเค", "ใช่เลย"}
    no_words = {"ไม่", "no", "ไม่ใช่", "ผิด", "แก้", "แก้ไข", "เปลี่ยน"}

    if text.lower() in yes_words:
        pending = user.get("pending_iux_id")

        # ถ้า IUX ID นี้ verified บน platform อื่นอยู่แล้ว → verify ทันทีไม่ต้องรอ email
        existing = db.get_all_users_by_iux_id(pending)
        already_verified = any(
            u.get("status") == "verified" and u.get("user_id") != user_id
            for u in existing
        )

        if already_verified:
            db.upsert_user(user_id, iux_user_id=pending,
                           pending_iux_id=None, status="verified", state="done",
                           pending_notified=True)
            reply(reply_token, _VERIFY_MSG)
        else:
            db.upsert_user(user_id, iux_user_id=pending,
                           pending_iux_id=None, status="pending", state="done",
                           pending_notified=True)
            reply(
                reply_token,
                f"✅ บันทึก IUX ID: {pending} เรียบร้อยครับ\n\n"
                "⏳ รอ Admin ยืนยันสักครู่นะครับ 🙏",
            )
            display_name = user.get(
                "display_name") or get_display_name(user_id) or user_id
            notify_msg = (
                f"🔔 มี User ใหม่รอยืนยัน!\n\n"
                f"ชื่อ LINE  : {display_name}\n"
                f"IUX User ID: {pending}\n\n"
                f"✅ ยืนยัน: /verify {pending}\n"
                f"❌ ปฏิเสธ: /reject {pending}"
            )
            push(ADMIN_LINE_USER_ID, notify_msg)
            # Notify Facebook admins too
            try:
                from facebook_messenger import fb_send as _fb_send
                for admin in db.get_admin_users():
                    if admin.get("platform") == "facebook":
                        try:
                            _fb_send(admin["user_id"], notify_msg)
                        except Exception as _e:
                            logger.error("FB admin notify failed: %s", _e)
            except Exception as _e:
                logger.error("get_admin_users failed: %s", _e)

    elif text.lower() in no_words:
        db.upsert_user(user_id, pending_iux_id=None, state="waiting_iux")
        reply(reply_token, "โอเคครับ กรุณาส่ง IUX User ID ใหม่ได้เลยครับ 😊")

    else:
        reply(reply_token, "กรุณาตอบ 'ใช่' หรือ 'ไม่' ครับ")


def _handle_done(user: dict, reply_token: str) -> None:
    status = user.get("status")
    if status == "pending":
        if not user.get("pending_notified"):
            reply(
                reply_token,
                "⏳ กำลังรอ Admin ยืนยัน IUX User ID ของคุณอยู่ครับ\nจะแจ้งให้ทราบเมื่อผ่านแล้ว 🙏",
            )
            db.upsert_user(user["user_id"], pending_notified=True)
        return
    if status == "verified":
        return
    elif status == "rejected":
        db.upsert_user(
            user["user_id"], status="new", state="waiting_iux",
            iux_user_id=None, pending_iux_id=None, pending_notified=False,
        )
        reply(
            reply_token,
            "❌ IUX User ID ไม่ผ่านการยืนยันครับ\n\n"
            "กรุณาส่ง IUX User ID ใหม่ได้เลยครับ\n"
            "(ตรวจสอบว่าสมัคร IUX ผ่าน affiliate link ของ TradingTP แล้วนะครับ)",
        )


# ---------------------------------------------------------------------------
# User signal request handler
# ---------------------------------------------------------------------------

_BUSY_MSG = (
    "⏳ ขณะนี้มีผู้ใช้งานระบบ AI จำนวนมาก\n"
    "กรุณาลองพิมพ์ /signal ใหม่อีกครั้งในอีก 1-2 นาทีครับ 🙏"
)


def _handle_user_signal(user: dict, reply_token: str) -> None:
    """Handle /signal command for a verified LINE user — one request per day."""
    import pytz
    from datetime import datetime
    tz = pytz.timezone("Asia/Bangkok")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    if user.get("last_signal_date") == today:
        reply(reply_token, "⚠️ คุณขอดู signal แล้ววันนี้ครับ\nกลับมาใหม่พรุ่งนี้ได้เลย 📅")
        return

    from signal_gen import generate_gold_analysis
    try:
        signal = generate_gold_analysis()
    except Exception as e:
        logger.error("Signal generation failed for %s: %s", user["user_id"], e)
        reply(reply_token, _BUSY_MSG)
        return

    if len(signal) < 200:
        logger.warning(
            "Signal too short for %s (%d chars) — not counting as used", user["user_id"], len(signal))
        reply(reply_token, _BUSY_MSG)
        return

    db.upsert_user(user["user_id"], platform="line", last_signal_date=today)
    reply(reply_token, signal)


# ---------------------------------------------------------------------------
# Admin command handler
# ---------------------------------------------------------------------------

_VERIFY_MSG = (
    "🎉 ยืนยันเรียบร้อยแล้วครับ!\n\n"
    "กลุ่มไลน์: https://line.me/ti/g2/2qPd6fIG5bY4P04_uKo_0sLKLDvqqTsAILh5Qg"
    "?utm_source=invitation&utm_medium=link_copy&utm_campaign=default\n\n"
    "📊 วิธีดู Daily Signal:\n"
    "พิมพ์ /signal ในแชทนี้เพื่อดู signal ทองคำประจำวัน (วันละ 1 ครั้ง)\n\n"
    "Strategy / Pine Script\n"
    "https://github.com/Kobayashi-UwU/trading_tp/tree/main/strategy\n\n"
    "Prompt\n"
    "https://github.com/Kobayashi-UwU/trading_tp/tree/main/prompt\n\n"
    "Code\n"
    "https://github.com/Kobayashi-UwU/trading_tp/tree/main/code"
)


def _handle_admin(text: str, reply_token: str) -> None:
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/verifyme", "/vertifyme") and arg:
        iux_id = arg.strip()
        try:
            display_name = get_display_name(ADMIN_LINE_USER_ID)
            db.upsert_user(
                ADMIN_LINE_USER_ID,
                iux_user_id=iux_id,
                status="verified",
                state="done",
                display_name=display_name,
            )
            reply(reply_token,
                  f"✅ ลงทะเบียนตัวเองเรียบร้อยแล้วครับ\n"
                  f"IUX ID: {iux_id}\n"
                  f"พิมพ์ /signal เพื่อดู signal ทองคำประจำวันได้เลยครับ 📊")
        except Exception as e:
            reply(reply_token, f"❌ Error: {str(e)}")

    elif cmd == "/addpending" and arg:
        iux_id = arg.strip()
        try:
            existing = db.get_user_by_iux_id(iux_id)
            if existing:
                reply(
                    reply_token,
                    f"⚠️ IUX ID: {iux_id} มีในระบบแล้ว (status: {existing.get('status')})",
                )
            else:
                fake_line_id = f"MANUAL_{iux_id}"
                db.upsert_user(fake_line_id, iux_user_id=iux_id, status="pending",
                               state="done", display_name=f"[Manual] {iux_id}")
                reply(reply_token,
                      f"✅ เพิ่ม IUX ID: {iux_id} เข้าระบบแล้ว\n"
                      f"ใช้ /verify {iux_id} เพื่อยืนยันได้เลย")
        except Exception as e:
            reply(reply_token, f"❌ Error: {str(e)}")

    elif cmd in ("/verify", "/vertify") and arg:
        users = db.get_all_users_by_iux_id(arg)
        if users:
            for u in users:
                db.upsert_user(
                    u["user_id"], platform=u["platform"], status="verified")
                push_to_user(u, _VERIFY_MSG)
                if u.get("platform") == "facebook" and not u.get("notification_token"):
                    try:
                        from facebook_messenger import fb_send_recurring_opt_in
                        fb_send_recurring_opt_in(u["user_id"])
                    except Exception as e:
                        logger.warning(
                            "Could not send recurring opt-in to %s: %s", u["user_id"], e)
            platforms = ", ".join(u["platform"] for u in users)
            reply(reply_token, f"✅ Verified IUX ID: {arg} ({platforms})")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/reject" and arg:
        users = db.get_all_users_by_iux_id(arg)
        if users:
            for u in users:
                db.upsert_user(
                    u["user_id"], platform=u["platform"], status="rejected")
                push_to_user(
                    u,
                    "❌ IUX User ID ไม่ผ่านการยืนยันครับ\n\n"
                    "กรุณาตรวจสอบว่าสมัคร IUX ผ่าน affiliate link ของ TradingTP\n"
                    "แล้วส่ง ID มาใหม่ได้เลยครับ 🙏",
                )
            reply(reply_token, f"❌ Rejected IUX ID: {arg}")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/update" and arg:
        parts_arg = arg.split()
        if len(parts_arg) != 2:
            reply(
                reply_token, "❌ รูปแบบไม่ถูกต้อง\nใช้: /update [iux_id_เก่า] [iux_id_ใหม่]")
            return
        old_id, new_id = parts_arg
        users = db.get_all_users_by_iux_id(old_id)
        real_users = [
            u for u in users if not u["user_id"].startswith("MANUAL_")]
        all_targets = real_users if real_users else users
        if all_targets:
            for u in all_targets:
                db.update_iux_id(u["user_id"], new_id,
                                 platform=u.get("platform", "line"))
            platforms = ", ".join(u.get("platform", "line")
                                  for u in all_targets)
            reply(reply_token,
                  f"✅ อัปเดต IUX ID เรียบร้อย ({len(all_targets)} record)\n"
                  f"เก่า: {old_id}\nใหม่: {new_id}\nPlatform: {platforms}")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {old_id} ในระบบ")

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
            reply(reply_token,
                  f"🔍 ค้นหา '{arg}' พบ {len(users)} คน\n\n" + "\n\n".join(lines))
        else:
            reply(reply_token, f"❌ ไม่พบชื่อที่ค้นหา: '{arg}'")

    elif cmd == "/info" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            verified_at = user.get("verified_at", "-") or "-"
            created_at = user.get("created_at", "-") or "-"
            reply(reply_token,
                  f"📋 ข้อมูล User\n\n"
                  f"ชื่อ         : {user.get('display_name', '-')}\n"
                  f"IUX User ID  : {user.get('iux_user_id', '-')}\n"
                  f"Platform     : {user.get('platform', '-')}\n"
                  f"User ID      : {user.get('user_id', '-')}\n"
                  f"Status       : {user.get('status', '-')}\n"
                  f"State        : {user.get('state', '-')}\n"
                  f"สมัครวันที่  : {created_at[:10] if len(created_at) > 10 else created_at}\n"
                  f"Verify วันที่: {verified_at[:10] if len(verified_at) > 10 else verified_at}")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/reset" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            db.reset_user(user["user_id"],
                          platform=user.get("platform", "line"))
            reply(reply_token, f"🔄 Reset user IUX ID: {arg} แล้ว")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {arg}")

    elif cmd == "/list":
        users = db.get_all_users()
        verified = [u for u in users if u["status"] == "verified"]
        pending = [u for u in users if u["status"] == "pending"]
        line_v = sum(1 for u in verified if u.get("platform") == "line")
        fb_v = sum(1 for u in verified if u.get("platform") == "facebook")
        pending_str = "\n".join(
            f"  • {u['iux_user_id']} [{u.get('platform', 'line')}]" for u in pending
        ) or "  (ไม่มี)"
        reply(
            reply_token,
            f"📊 สรุป Users ทั้งหมด\n\n"
            f"✅ Verified: {len(verified)} คน (LINE: {line_v}, FB: {fb_v})\n"
            f"⏳ Pending: {len(pending)} คน\n"
            f"👥 Total: {len(users)} คน\n\n"
            f"Pending IDs:\n{pending_str}",
        )

    elif cmd == "/signal":
        reply(reply_token, "⏳ กำลังวิเคราะห์ทองคำ รอแป๊บนึงครับ...")
        from signal_gen import generate_gold_analysis
        try:
            signal = generate_gold_analysis()
            push(ADMIN_LINE_USER_ID, signal)
        except Exception as e:
            logger.error("Admin signal failed: %s", e)
            push(ADMIN_LINE_USER_ID, f"❌ Generate signal ล้มเหลว: {e}")

    elif cmd == "/dailycheck":
        reply(reply_token, "⏳ กำลังวิเคราะห์ทองคำ รอแป๊บนึงครับ...")
        from signal_gen import generate_gold_analysis
        try:
            analysis = generate_gold_analysis()
            push(ADMIN_LINE_USER_ID, analysis)
        except Exception as e:
            push(ADMIN_LINE_USER_ID, f"❌ วิเคราะห์ทองไม่สำเร็จ: {e}")

    elif cmd == "/autoverifynow":
        reply(reply_token, "⏳ กำลังเช็ค email จาก IUX ทันที...")
        from gmail_poller import poll_all_iux_emails
        try:
            verified = poll_all_iux_emails(configuration, db)
            if verified:
                lines = "\n".join(
                    f"{i+1}. IUX ID: {v['iux_id']}  ชื่อ {v['platform'].upper()}: {v['display_name']}"
                    for i, v in enumerate(verified)
                )
                push(ADMIN_LINE_USER_ID,
                     f"✅ เช็ค email เสร็จแล้ว\n\n"
                     f"User ใหม่ที่ verify แล้ว:\n{lines}")
            else:
                push(ADMIN_LINE_USER_ID,
                     "✅ เช็ค email เสร็จแล้ว\nไม่พบ user ใหม่ที่รอ verify")
        except Exception as e:
            push(ADMIN_LINE_USER_ID, f"❌ Auto-verify ล้มเหลว: {e}")

    elif cmd == "/broadcast":
        reply(reply_token, "⏳ กำลัง broadcast signal ไปหา verified users...")
        from scheduler import broadcast_signal
        broadcast_signal(configuration, db)

    elif cmd == "/help":
        reply(
            reply_token,
            "📋 Admin Commands:\n\n"
            "/verifyme [IUX_ID]    — ลงทะเบียนตัวเองเป็น verified user\n"
            "/addpending [ID]      — เพิ่ม IUX ID เข้าระบบ (manual)\n"
            "/verify [ID]          — ยืนยัน user (ทุก platform)\n"
            "/reject [ID]          — ปฏิเสธ user (ทุก platform)\n"
            "/update [เก่า] [ใหม่]  — แก้ IUX ID ของ user\n"
            "/reset [ID]           — reset ให้ user ส่ง ID ใหม่\n"
            "/info [ID]            — ดูข้อมูล user\n"
            "/findname [ชื่อ]       — ค้นหา user จากชื่อ\n"
            "/list                 — ดู users ทั้งหมด\n"
            "/signal              — generate signal (admin: ดูทันที / user: ดูได้วันละ 1 ครั้ง)\n"
            "/dailycheck          — วิเคราะห์ทองคำทันที\n"
            "/broadcast           — broadcast ไปหา verified users\n"
            "/autoverifynow       — เช็ค email IUX และ verify ทันที\n"
            "/help                — แสดง commands",
        )

    else:
        reply(reply_token, "❓ ไม่รู้จัก command นี้ครับ\nพิมพ์ /help เพื่อดูคำสั่งทั้งหมด")


# ---------------------------------------------------------------------------
# Start scheduler & app
# ---------------------------------------------------------------------------

_scheduler = start_scheduler(configuration, db)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
