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
        ReplyMessageRequest(reply_token=reply_token,
                            messages=[TextMessage(text=text)])
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

def get_display_name(user_id: str) -> str:
    try:
        profile = messaging_api().get_profile(user_id)
        return profile.display_name
    except Exception:
        return ""


@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    display_name = get_display_name(user_id)
    db.upsert_user(user_id, status="new", state="waiting_iux",
                   display_name=display_name, pending_notified=False)
    reply(
        event.reply_token,
        "สวัสดีครับ! ยินดีต้อนรับสู่ TradingTP 🎉\n\n"
        "เพื่อรับ Daily Trend ฟรีทุกเช้า 8:00 น. / Prompt หรือ โค้ดต่างๆ กรุณาส่ง IUX User ID ของคุณมาได้เลยครับ\n\n"
        "💡 IUX User ID คือตัวเลข 6 หรือ 8 หลักที่แสดงอยู่ในหน้า Profile ของ IUX ครับ\n\n"
        "หรือหากยังไม่มีบัญชี IUX สามารถสมัครฟรีได้ที่ https://iux.com/en/register?code=IuyjFrlz เลยครับ\n\n"
        "สำหรับคนที่มีบัญชี iux อยู่แล้ว ต้องโอนย้ายก่อนนะครับตามลิงค์นี้\n"
        "👇👇👇\n"
        "https://www.iux.com/en/dashboard/ib-transfers-request\n\n"
        "Partner referral code: IuyjFrlz\n\n"
        "หลังจากโอนย้ายเสร็จแล้วแจ้งผมได้เลยครับผม"
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
        reply(reply_token, "กรุณาส่ง IUX User ID ของคุณเพื่อรับสิทธิ์ Daily Signal / Code / Prompt ครับ")
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
        reply(reply_token,
              f"IUX User ID: {iux_id} ใช่ไหมครับ? (พิมพ์ ใช่ หรือ ไม่)")
    # ถ้าไม่มี IUX ID ในข้อความ → เงียบ ไม่ตอบซ้ำ


def _handle_confirming(user_id: str, text: str, reply_token: str, user: dict) -> None:
    yes_words = {"ใช่", "yes", "ใช่ครับ", "ใช่ค่ะ",
                 "ถูก", "ถูกต้อง", "ok", "okay", "โอเค", "ใช่เลย"}
    no_words = {"ไม่", "no", "ไม่ใช่", "ผิด", "แก้", "แก้ไข", "เปลี่ยน"}

    if text.lower() in yes_words:
        pending = user.get("pending_iux_id")
        db.upsert_user(user_id, iux_user_id=pending,
                       pending_iux_id=None, status="pending", state="done",
                       pending_notified=True)
        reply(
            reply_token,
            f"✅ บันทึก IUX ID: {pending} เรียบร้อยครับ\n\n"
            "⏳ รอ Admin ยืนยันสักครู่นะครับ 🙏",
        )
        display_name = user.get("display_name") or user_id
        push(
            ADMIN_LINE_USER_ID,
            f"🔔 มี User ใหม่รอยืนยัน!\n\n"
            f"ชื่อ LINE  : {display_name}\n"
            f"IUX User ID: {pending}\n\n"
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
        if not user.get("pending_notified"):
            reply(
                reply_token, "⏳ กำลังรอ Admin ยืนยัน IUX User ID ของคุณอยู่ครับ\nจะแจ้งให้ทราบเมื่อผ่านแล้ว 🙏")
            db.upsert_user(user["line_user_id"], pending_notified=True)
        return
    if status == "verified":
        return  # verified แล้วไม่จำเป็นต้องตอบทุก message
    elif status == "rejected":
        db.upsert_user(user["line_user_id"], status="new", state="waiting_iux",
                       iux_user_id=None, pending_iux_id=None, pending_notified=False)
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
                  f"คุณจะได้รับ Daily Signal ทุกเช้า 8:00 น. ด้วยครับ 📊")
        except Exception as e:
            reply(reply_token, f"❌ Error: {str(e)}")

    elif cmd == "/addpending" and arg:
        iux_id = arg.strip()
        try:
            existing = db.get_user_by_iux_id(iux_id)
            if existing:
                reply(
                    reply_token, f"⚠️ IUX ID: {iux_id} มีในระบบแล้ว (status: {existing.get('status')})")
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
        user = db.get_user_by_iux_id(arg)
        if user:
            db.upsert_user(user["line_user_id"], status="verified")
            push(
                user["line_user_id"],
                "🎉 ยืนยันเรียบร้อยแล้วครับ!\n\n"
                "กลุ่มไลน์: https://line.me/ti/g2/2qPd6fIG5bY4P04_uKo_0sLKLDvqqTsAILh5Qg?utm_source=invitation&utm_medium=link_copy&utm_campaign=default\n\n"
                "คุณจะได้รับ Daily Trading Signal ทุกเช้า 8:00 น.📈\n\n"
                "Pstrategy / Pine Script\n"
                "https: // github.com/Kobayashi-UwU/trading_tp/tree/main/strategy\n\n"
                "Prompt\n"
                "https: // github.com/Kobayashi-UwU/trading_tp/tree/main/prompt\n\n"
                "Code\n"
                "https: // github.com/Kobayashi-UwU/trading_tp/tree/main/code",
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

    elif cmd == "/update" and arg:
        parts_arg = arg.split()
        if len(parts_arg) != 2:
            reply(
                reply_token, "❌ รูปแบบไม่ถูกต้อง\nใช้: /update [iux_id_เก่า] [iux_id_ใหม่]")
            return
        old_id, new_id = parts_arg
        user = db.get_user_by_iux_id(old_id)
        if user:
            db.update_iux_id(user["line_user_id"], new_id)
            reply(reply_token,
                  f"✅ อัปเดต IUX ID เรียบร้อย\n"
                  f"เก่า: {old_id}\n"
                  f"ใหม่: {new_id}\n"
                  f"Status: {user.get('status', '?')} (คงเดิม)")
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
                    f"   Status: {u.get('status', '-')}"
                )
            reply(
                reply_token, f"🔍 ค้นหา '{arg}' พบ {len(users)} คน\n\n" + "\n\n".join(lines))
        else:
            reply(reply_token, f"❌ ไม่พบชื่อที่ค้นหา: '{arg}'")

    elif cmd == "/info" and arg:
        user = db.get_user_by_iux_id(arg)
        if user:
            verified_at = user.get("verified_at", "-") or "-"
            created_at = user.get("created_at", "-") or "-"
            reply(reply_token,
                  f"📋 ข้อมูล User\n\n"
                  f"ชื่อ LINE    : {user.get('display_name', '-')}\n"
                  f"IUX User ID : {user.get('iux_user_id', '-')}\n"
                  f"LINE User ID: {user.get('line_user_id', '-')}\n"
                  f"Status      : {user.get('status', '-')}\n"
                  f"State       : {user.get('state', '-')}\n"
                  f"สมัครวันที่ : {created_at[:10] if len(created_at) > 10 else created_at}\n"
                  f"Verify วันที่: {verified_at[:10] if len(verified_at) > 10 else verified_at}")
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
        pending_str = "\n".join(
            f"  • {u['iux_user_id']}" for u in pending) or "  (ไม่มี)"
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

    elif cmd == "/dailycheck":
        reply(reply_token, "⏳ กำลังวิเคราะห์ทองคำ รอแป๊บนึงครับ...")
        from signal_gen import generate_gold_analysis
        try:
            analysis = generate_gold_analysis()
            push(ADMIN_LINE_USER_ID, analysis)
        except Exception as e:
            push(ADMIN_LINE_USER_ID, f"❌ วิเคราะห์ทองไม่สำเร็จ: {e}")

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
            "/verify [ID]          — ยืนยัน user\n"
            "/reject [ID]          — ปฏิเสธ user\n"
            "/update [เก่า] [ใหม่]  — แก้ IUX ID ของ user\n"
            "/reset [ID]           — reset ให้ user ส่ง ID ใหม่\n"
            "/info [ID]            — ดูข้อมูล user\n"
            "/findname [ชื่อ]       — ค้นหา user จากชื่อ LINE\n"
            "/list                 — ดู users ทั้งหมด\n"
            "/signal              — generate signal ให้ตัวเอง\n"
            "/dailycheck          — วิเคราะห์ทองคำทันที\n"
            "/broadcast           — broadcast ไปหา verified users\n"
            "/help                — แสดง commands",
        )

    else:
        reply(
            reply_token,
            "❓ ไม่รู้จัก command นี้ครับ\nพิมพ์ /help เพื่อดูคำสั่งทั้งหมด",
        )


# ---------------------------------------------------------------------------
# Start scheduler & app
# ---------------------------------------------------------------------------

start_scheduler(configuration, db)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
