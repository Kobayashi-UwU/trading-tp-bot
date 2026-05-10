#!/opt/homebrew/bin/python3.10
"""
test_chat.py — Local chat simulator สำหรับทดสอบ TradingTP bot
รัน: python3.10 test_chat.py
"""

import os
import sys

# โหลด .env จริงก่อน แล้วค่อย setdefault สำหรับค่าที่ไม่มี
from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_xxx")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token_xxx")
os.environ.setdefault("ADMIN_LINE_USER_ID", "ADMIN_TEST_001")

ADMIN_ID = os.environ["ADMIN_LINE_USER_ID"]

# ── Mock LINE SDK และ Flask เพื่อให้ import main.py ผ่านได้ ─────────────────
from unittest.mock import MagicMock


def _passthrough(*args, **kwargs):
    """Decorator ที่คืน function เดิมโดยไม่แตะ"""
    def decorator(func):
        return func
    return decorator


# Flask mock — @app.route ต้องเป็น pass-through
_mock_app = MagicMock()
_mock_app.route.side_effect = _passthrough
_mock_flask = MagicMock()
_mock_flask.Flask.return_value = _mock_app
sys.modules["flask"] = _mock_flask

# LINE SDK mock — @handler.add ต้องเป็น pass-through เพื่อให้ handle_message ไม่ถูก replace
_mock_handler_instance = MagicMock()
_mock_handler_instance.add.side_effect = _passthrough
_mock_wh_class = MagicMock(return_value=_mock_handler_instance)
_mock_linebot_v3 = MagicMock()
_mock_linebot_v3.WebhookHandler = _mock_wh_class
sys.modules["linebot"] = MagicMock()
sys.modules["linebot.v3"] = _mock_linebot_v3
sys.modules["linebot.v3.exceptions"] = MagicMock()
sys.modules["linebot.v3.messaging"] = MagicMock()
sys.modules["linebot.v3.webhooks"] = MagicMock()

# APScheduler mock
sys.modules["apscheduler"] = MagicMock()
sys.modules["apscheduler.schedulers"] = MagicMock()
sys.modules["apscheduler.schedulers.background"] = MagicMock()
sys.modules["apscheduler.triggers"] = MagicMock()
sys.modules["apscheduler.triggers.cron"] = MagicMock()

# ── Import main (หลัง mock พร้อมแล้ว) ────────────────────────────────────────
import main as bot  # noqa: E402

# ── Colors ───────────────────────────────────────────────────────────────────
R = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
GREY = "\033[90m"
RED = "\033[91m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"

# ── State ─────────────────────────────────────────────────────────────────────
chat_log: list[tuple[str, str]] = []
current_user_id = "TEST_USER_001"


# ── Patch bot.reply / bot.push ────────────────────────────────────────────────
def _mock_reply(_token: str, text: str) -> None:
    chat_log.append(("BOT", text))
    _render()


def _mock_push(user_id: str, text: str) -> None:
    tag = "PUSH→ADMIN" if user_id == ADMIN_ID else f"PUSH→{user_id[:12]}"
    chat_log.append((tag, text))
    _render()


def _mock_display_name(uid: str) -> str:
    return f"[TestUser-{uid[-6:]}]"


bot.reply = _mock_reply
bot.push = _mock_push
bot.get_display_name = _mock_display_name


# ── Fake LINE event ───────────────────────────────────────────────────────────
class _Src:
    def __init__(self, uid):
        self.user_id = uid


class _Msg:
    def __init__(self, txt):
        self.text = txt


class _Evt:
    def __init__(self, uid, txt):
        self.source = _Src(uid)
        self.message = _Msg(txt)
        self.reply_token = "fake_reply_token"


# ── Render ────────────────────────────────────────────────────────────────────
def _render():
    os.system("clear")
    is_admin = current_user_id == ADMIN_ID
    role = f"{YELLOW}[ADMIN]{R}" if is_admin else f"{CYAN}[USER]{R}"
    print(f"{BOLD}{'─' * 58}{R}")
    print(f"  🤖  TradingTP Bot — Local Test Chat")
    print(f"  👤  {role} {GREY}{current_user_id}{R}")
    print(f"{BOLD}{'─' * 58}{R}\n")

    for sender, text in chat_log[-40:]:
        _print_bubble(sender, text)

    print()


def _print_bubble(sender: str, text: str):
    if sender == "USER":
        indent = " " * 11
        label = f"  {CYAN}You  ›{R} {CYAN}"
        end = R
    elif sender == "BOT":
        indent = " " * 11
        label = f"  {GREEN}Bot  ›{R} {GREEN}"
        end = R
    elif sender == "PUSH→ADMIN":
        indent = " " * 16
        label = f"  {YELLOW}Push → Admin ›{R} {YELLOW}"
        end = R
    elif sender == "SYSTEM":
        indent = " " * 11
        label = f"  {GREY}Sys  ›{R} {GREY}"
        end = R
    else:
        indent = " " * 16
        label = f"  {BLUE}{sender} ›{R} {BLUE}"
        end = R

    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i == 0:
            print(f"{label}{line}{end}")
        else:
            print(f"{indent}{GREY if sender == 'SYSTEM' else (GREEN if sender == 'BOT' else YELLOW if sender == 'PUSH→ADMIN' else CYAN)}{line}{R}")
    print()


# ── Send message to bot ───────────────────────────────────────────────────────
def _send(text: str):
    chat_log.append(("USER", text))
    evt = _Evt(current_user_id, text)
    try:
        bot.handle_message(evt)
    except Exception as e:
        chat_log.append(("SYSTEM", f"❌ Error: {type(e).__name__}: {e}"))
        _render()


# ── DB status helper ──────────────────────────────────────────────────────────
def _show_status():
    user = bot.db.get_user(current_user_id)
    print(f"\n{BOLD}DB State — {current_user_id}{R}")
    if user:
        important = ["status", "state", "iux_user_id",
                     "pending_iux_id", "pending_notified", "display_name"]
        for k in important:
            v = user.get(k)
            color = YELLOW if v else GREY
            print(f"  {GREY}{k:<20}{R}{color}{v}{R}")
    else:
        print(f"  {YELLOW}(ยังไม่มีใน DB){R}")
    print()


# ── Help ──────────────────────────────────────────────────────────────────────
def _show_help():
    print(f"""
{BOLD}Simulator Commands:{R}
  {YELLOW}/user <id>{R}    — เปลี่ยน user ID  (เช่น /user TEST_USER_002)
  {YELLOW}/admin{R}        — switch เป็น Admin ({ADMIN_ID[:16]}...)
  {YELLOW}/status{R}       — ดู DB state ของ user ปัจจุบัน
  {YELLOW}/clear{R}        — ล้าง chat log
  {YELLOW}/follow{R}       — จำลอง event Follow (user add OA ครั้งแรก)
  {YELLOW}/help{R}         — แสดงคำสั่งนี้
  {YELLOW}/quit{R}         — ออก

{BOLD}วิธีทดสอบเบื้องต้น:{R}
  1. พิมพ์ข้อความเหมือน user ปกติ เช่น ส่ง IUX User ID: {CYAN}123456{R}
  2. /admin แล้วพิมพ์ /verify 123456 เพื่อยืนยัน
  3. /user TEST_USER_001 เพื่อกลับไปเช็คว่า user ได้รับแจ้ง verified
""")


# ── Follow event helper ────────────────────────────────────────────────────────
class _FollowEvt:
    def __init__(self, uid):
        self.source = _Src(uid)
        self.reply_token = "fake_follow_token"


def _simulate_follow():
    chat_log.append(("SYSTEM", f"[Follow event] {current_user_id}"))
    try:
        bot.handle_follow(_FollowEvt(current_user_id))
    except Exception as e:
        chat_log.append(("SYSTEM", f"❌ Error: {type(e).__name__}: {e}"))
    _render()


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    global current_user_id
    _render()
    print(f"{GREY}พิมพ์ /help เพื่อดูคำสั่ง  |  Ctrl+C เพื่อออก{R}\n")

    while True:
        try:
            prompt = (f"{YELLOW}Admin > {R}"
                      if current_user_id == ADMIN_ID
                      else f"{CYAN}You  > {R}")
            text = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not text:
            continue

        if text == "/quit":
            print("Bye!")
            break
        elif text == "/help":
            _show_help()
        elif text == "/clear":
            chat_log.clear()
            _render()
        elif text == "/admin":
            current_user_id = ADMIN_ID
            chat_log.append(("SYSTEM", "Switched → ADMIN"))
            _render()
        elif text == "/status":
            _show_status()
        elif text == "/follow":
            _simulate_follow()
        elif text.startswith("/user "):
            new_id = text[6:].strip()
            if new_id:
                current_user_id = new_id
                chat_log.append(("SYSTEM", f"Switched → {new_id}"))
                _render()
        else:
            _send(text)


if __name__ == "__main__":
    main()
