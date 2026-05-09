import logging
import os
import re

from dotenv import load_dotenv
from flask import Flask, abort, request
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
from linebot.v3.webhooks import UnfollowEvent, FollowEvent, MessageEvent, TextMessageContent

from db import Database
from scheduler import start_scheduler

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_LINE_USER_ID = os.environ["ADMIN_LINE_USER_ID"]

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
db = Database()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def messaging_api() -> MessagingApi:
    return MessagingApi(ApiClient(configuration))


def reply(reply_token: str, text: str) -> None:
    messaging_api().reply_message(
        ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=text)])
    )


def push(user_id: str, text: str) -> None:
    messaging_api().push_message(
        PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
    )


def extract_iux_id(text: str) -> str | None:
    """ดึงตัวเลข 6 หรือ 8 หลักจากข้อความ"""
    matches = re.findall(r"\b(\d{6}|\d{8})\b", text)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Webhook endpoint
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


@app.route("/health", methods=["GET"])
def health():
    return "OK"


# ---------------------------------------------------------------------------
# Event: Follow (user adds the OA)
# ---------------------------------------------------------------------------

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    db.upsert_user(user_id, status="new", state="waiting_iux")
    reply(
        event.reply_token,
        "สวัสดีครับ! ยินดีต้อนรับสู่ TradingTP Signal Bot 🎉\n\n"
        "เพื่อรับ Daily Trading Signal ฟรีทุกเช้า 8:00 น.\n"
        "กรุณาส่ง IUX User ID ของคุณมาได้เลยครับ\n\n"
        "💡 IUX User ID คือตัวเลข 6 หรือ 8 หลัก\n"
        "ที่แสดงอยู่ในหน้า Profile ของ IUX ครับ",
    )


# ---------------------------------------------------------------------------
# Event: Block (user blocks the OA)
# ---------------------------------------------------------------------------

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    db.upsert_user(event.source.user_id, status="blocked")


# ---------------------------------------------------------------------------
# Event: Message
# ---------------------------------------------------------------------------

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    reply_token = event.reply_token

    # Admin commands — ไม่ผ่าน flow ปกติ
    if user_id == ADMIN_LINE_USER_ID:
        _handle_admin(text, reply_token)
        return

    user = db.get_user(user_id)
    if not user:
        db.upsert_user(user_id, status="new", state="waiting_iux")
        reply(reply_token, "กรุณาส่ง IUX User ID ของคุณเพื่อรับสิทธิ์ Daily Signal ครับ")
        return

    state = user.get("state", "waiting_iux")

    if state == "waiting_iux":
        _handle_waiting_iux(user_id, text, reply_token)

    elif state == "confirming":
        _handle_confirming(user_id, text, reply_token, user)

    elif state == "done":
        _handle_done(user, reply_token)


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------

def _handle_waiting_iux(user_id: str, text: str, reply_token: str) -> None:
    iux_id = extract_iux_id(text)
    if iux_id:
        db.upsert_user(user_id, pending_iux_id=iux_id, state="confirming")
        reply(
            reply_token,
            f"รับ IUX User ID: {iux_id} ครับ\n\nถูกต้องไหมครับ?\n✅ พิมพ์ 'ใช่'\n❌ พิมพ์ 'ไม่'",
        )
    else:
        reply(
            reply_token,
            "ไม่พบ IUX User ID ในข้อความครับ 🤔\n\n"
            "กรุณาส่งตัวเลข 6 หรือ 8 หลัก เช่น:\n"
            "• 123456\n• 12345678\n• นี่ครับ 123456",
        )


def _handle_confirming(user_id: str, text: str, reply_token: str, user: dict) -> None:
    yes_words = {"ใช่", "yes", "ใช่ครับ", "ใช่ค่ะ", "ถูก", "ถูกต้อง", "ok", "okay", "โอเค", "ใช่เลย"}
    no_words = {"ไม่", "no", "ไม่ใช่", "ผิด", "แก้", "แก้ไข", "เปลี่ยน"}

    if text.lower() in yes_words:
        pending = user.get("pending_iux_id")
        db.upsert_user(user_id, iux_user_id=pending, pending_iux_id=None, status="pending", state="done")
        reply(
            reply_token,
            f"✅ บันทึก IUX ID: {pending} เรียบร้อยครับ\n\n"
            "⏳ รอ Admin ยืนยันสักครู่นะครับ\n"
            "เมื่อผ่านแล้วจะได้รับ Signal ทุกเช้า 8:00 น. ครับ 🙏",
        )
        push(
            ADMIN_LINE_USER_ID,
            f"🔔 มี User ใหม่รอยืนยัน!\n\n"
            f"IUX User ID: {pending}\n"
            f"LINE User ID: {user_id}\n\n"
            f"✅ ยืนยัน: /verify {pending}\n"
            f"❌ ปฏิเสธ: /reject {pending}",
        )

    elif text.lower() in no_words:
        db.upsert_user(user_id, pending_iux_id=None, state="waiting_iux")
        reply(reply_token, "โอเคครับ กรุณาส่ง IUX User ID ใหม่ได้เลยครับ 😊")

    else:
        reply(reply_token, "กรุณาตอบ 'ใช่' หรือ 'ไม่' ครับ")


def _handle_done(user: dict, reply_token: str) -> None:
    status = user.get("status")
    if status == "pending":
        reply(reply_token, "⏳ กำลังรอ Admin ยืนยันอยู่ครับ รอสักครู่นะครับ 🙏")
    elif status == "verified":
        reply(reply_token, "✅ คุณได้รับสิทธิ์แล้วครับ! รอรับ Signal ทุกเช้า 8:00 น. ครับ 📊")
    elif status == "rejected":
        db.upsert_user(user["line_user_id"], status="new", state="waiting_iux",
                       iux_user_id=None, pending_iux_id=None)
        reply(
            reply_token,
            "❌ IUX User ID ไม่ผ่านการยืนยันครับ\n\n"
            "กรุณาส่ง IUX User ID ใหม่ได้เลยครับ\n"
            "(ตรวจสอบว่าสมัคร IUX ผ่าน affiliate link ของ TradingTP แล้วนะครับ)",
        )


# ---------------------------------------------------------------------------
# Admin command handler
# ---------------------------------------------------------------------------

def _handle_admin(text: str, reply_token: str) -> None:
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/verify" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            db.upsert_user(user["line_user_id"], status="verified")
            push(
                user["line_user_id"],
                "🎉 ยืนยันเรียบร้อยแล้วครับ!\n\n"
                "คุณจะได้รับ Daily Trading Signal ทุกเช้า 8:00 น.\n"
                "ขอให้เทรดสำเร็จนะครับ 📈",
            )
            reply(reply_token, f"✅ Verified IUX ID: {arg} เรียบร้อยแล้ว")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/reject" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            db.upsert_user(user["line_user_id"], status="rejected")
            push(
                user["line_user_id"],
                "❌ IUX User ID ไม่ผ่านการยืนยันครับ\n\n"
                "กรุณาตรวจสอบว่าสมัคร IUX ผ่าน affiliate link ของ TradingTP\n"
                "แล้วส่ง ID มาใหม่ได้เลยครับ 🙏",
            )
            reply(reply_token, f"❌ Rejected IUX ID: {arg}")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {arg} ในระบบ")

    elif cmd == "/reset" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            db.reset_user(user["line_user_id"])
            reply(reply_token, f"🔄 Reset user IUX ID: {arg} แล้ว")
        else:
            reply(reply_token, f"❌ ไม่พบ IUX ID: {arg}")

    elif cmd == "/list":
        users = db.get_all_users()
        verified = [u for u in users if u["status"] == "verified"]
        pending = [u for u in users if u["status"] == "pending"]
        pending_str = "\n".join(f"  • {u['iux_user_id']}" for u in pending) or "  (ไม่มี)"
        reply(
            reply_token,
            f"📊 สรุป Users ทั้งหมด\n\n"
            f"✅ Verified: {len(verified)} คน\n"
            f"⏳ Pending: {len(pending)} คน\n"
            f"👥 Total: {len(users)} คน\n\n"
            f"Pending IDs:\n{pending_str}",
        )

    elif cmd == "/signal":
        reply(reply_token, "⏳ กำลัง generate signal... รอแป๊บนึงครับ")
        from signal_gen import generate_signal
        try:
            signal = generate_signal()
            push(ADMIN_LINE_USER_ID, signal)
        except Exception as e:
            push(ADMIN_LINE_USER_ID, f"❌ Generate signal ล้มเหลว: {e}")

    elif cmd == "/broadcast":
        reply(reply_token, "⏳ กำลัง broadcast signal ไปหา verified users...")
        from scheduler import broadcast_signal
        broadcast_signal(configuration, db)

    elif cmd == "/help":
        reply(
            reply_token,
            "📋 Admin Commands:\n\n"
            "/verify [IUX_ID] — ยืนยัน user\n"
            "/reject [IUX_ID] — ปฏิเสธ user\n"
            "/reset [IUX_ID]  — reset user ให้ส่ง ID ใหม่\n"
            "/list            — ดู users ทั้งหมด\n"
            "/signal          — generate และส่ง signal ให้ตัวเอง\n"
            "/broadcast       — broadcast ไปหา verified users ทันที\n"
            "/help            — แสดง commands",
        )
    else:
        reply(reply_token, "❓ ไม่รู้จัก command พิมพ์ /help ดู commands ได้ครับ")


# ---------------------------------------------------------------------------
# Start scheduler & app
# ---------------------------------------------------------------------------

start_scheduler(configuration, db)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
