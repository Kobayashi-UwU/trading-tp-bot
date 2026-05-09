import os
from datetime import datetime

import requests
import pytz
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

PAIRS = {
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "BTCUSD": "BTC-USD",
}


def get_market_data() -> str:
    lines = []
    for name, ticker in PAIRS.items():
        try:
            df = yf.download(ticker, period="3d", interval="1h", progress=False)
            if df.empty:
                continue
            close = df["Close"].dropna()
            latest = float(close.iloc[-1].iloc[0]) if hasattr(close.iloc[-1], 'iloc') else float(close.iloc[-1])
            prev = float(close.iloc[-2].iloc[0]) if hasattr(close.iloc[-2], 'iloc') else float(close.iloc[-2])
            change_pct = (latest - prev) / prev * 100
            sign = "+" if change_pct >= 0 else ""
            lines.append(f"  {name}: {latest:.4f}  ({sign}{change_pct:.2f}%)")
        except Exception:
            pass
    return "\n".join(lines) if lines else "ไม่สามารถดึงข้อมูลตลาดได้"


def get_gold_data() -> dict:
    """ดึงข้อมูล XAUUSD แบบละเอียด หลายกรอบเวลา"""
    result = {}
    try:
        df_1h = yf.download("GC=F", period="5d", interval="1h", progress=False)
        df_1d = yf.download("GC=F", period="30d", interval="1d", progress=False)

        def s(series_val):
            """แปลง Series element เป็น float อย่างปลอดภัย"""
            v = series_val
            if hasattr(v, 'iloc'):
                v = v.iloc[0]
            return float(v)

        if not df_1h.empty:
            close_1h = df_1h["Close"].dropna()
            high_1h = df_1h["High"].dropna()
            low_1h = df_1h["Low"].dropna()

            latest = s(close_1h.iloc[-1])
            prev_1h = s(close_1h.iloc[-2])
            prev_24h = s(close_1h.iloc[-24]) if len(close_1h) >= 24 else s(close_1h.iloc[0])

            result["price"] = latest
            result["change_1h"] = (latest - prev_1h) / prev_1h * 100
            result["change_24h"] = (latest - prev_24h) / prev_24h * 100
            result["high_5d"] = s(high_1h.tail(5 * 24).max())
            result["low_5d"] = s(low_1h.tail(5 * 24).min())

            ema20 = s(close_1h.ewm(span=20).mean().iloc[-1])
            ema50 = s(close_1h.ewm(span=50).mean().iloc[-1])
            result["ema20_1h"] = ema20
            result["ema50_1h"] = ema50

            delta = close_1h.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            result["rsi_1h"] = s((100 - (100 / (1 + rs))).iloc[-1])

        if not df_1d.empty:
            close_1d = df_1d["Close"].dropna()
            result["high_30d"] = s(df_1d["High"].dropna().tail(30).max())
            result["low_30d"] = s(df_1d["Low"].dropna().tail(30).min())
            result["ema20_1d"] = s(close_1d.ewm(span=20).mean().iloc[-1])

    except Exception:
        pass
    return result


def _gemini(prompt: str, max_tokens: int = 800) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate_gold_analysis() -> str:
    """วิเคราะห์ทองคำ (XAUUSD) แบบละเอียดทันที"""
    affiliate_link = os.environ.get("IUX_AFFILIATE_LINK", "https://iux.com")

    bangkok = pytz.timezone("Asia/Bangkok")
    now = datetime.now(bangkok).strftime("%d %b %Y %H:%M")

    data = get_gold_data()

    if not data:
        return "❌ ไม่สามารถดึงข้อมูลราคาทองได้ในขณะนี้ครับ"

    ch1 = f"+{data['change_1h']:.2f}%" if data.get("change_1h", 0) >= 0 else f"{data['change_1h']:.2f}%"
    ch24 = f"+{data['change_24h']:.2f}%" if data.get("change_24h", 0) >= 0 else f"{data['change_24h']:.2f}%"

    data_text = f"""
ราคาปัจจุบัน: {data.get('price', 'N/A'):.2f} USD
เปลี่ยนแปลง 1h: {ch1}
เปลี่ยนแปลง 24h: {ch24}

Technical (1H):
  EMA20: {data.get('ema20_1h', 0):.2f}
  EMA50: {data.get('ema50_1h', 0):.2f}
  RSI14: {data.get('rsi_1h', 0):.1f}

แนวรับ-แนวต้าน:
  High 5 วัน: {data.get('high_5d', 0):.2f}
  Low 5 วัน:  {data.get('low_5d', 0):.2f}
  High 30 วัน: {data.get('high_30d', 0):.2f}
  Low 30 วัน:  {data.get('low_30d', 0):.2f}
  EMA20 Daily: {data.get('ema20_1d', 0):.2f}
"""

    prompt = f"""คุณเป็น AI Trading Analyst ของช่อง TradingTP เชี่ยวชาญด้านทองคำ (XAUUSD)

ข้อมูล XAUUSD ณ {now}:
{data_text}

วิเคราะห์สถานการณ์ทองคำวันนี้อย่างละเอียด ภาษาไทย โดยครอบคลุม:
1. Bias ตลาดวันนี้ (Bullish/Bearish/Sideways) พร้อมเหตุผลจาก EMA และ RSI
2. แนวรับ-แนวต้านสำคัญที่ควรจับตา
3. Setup เทรดที่ดีที่สุดวันนี้ (entry, TP, SL)
4. สิ่งที่ต้องระวัง

ใช้ format นี้:

🏅 XAUUSD Daily Check — {now}

📊 Bias: [Bullish/Bearish/Sideways]
💰 ราคาปัจจุบัน: [ราคา]

📈 การวิเคราะห์:
[วิเคราะห์ 3-4 บรรทัด อ้างอิงจาก EMA, RSI, price action]

🎯 แนวรับ-แนวต้าน:
• แนวต้าน: [ราคา]
• แนวรับ: [ราคา]

✅ Setup แนะนำ:
• [Buy/Sell] Zone: [ราคา]
• TP1: [ราคา]  TP2: [ราคา]
• SL: [ราคา]

⚠️ ระวัง: [สิ่งที่ต้องระวังวันนี้ 1-2 บรรทัด]

───────────────
เทรดผ่าน IUX รับ spread ต่ำสุด
👉 สมัครฟรี: {affiliate_link}"""

    return _gemini(prompt, max_tokens=800)


def generate_signal() -> str:
    affiliate_link = os.environ.get("IUX_AFFILIATE_LINK", "https://iux.com")

    bangkok = pytz.timezone("Asia/Bangkok")
    today = datetime.now(bangkok).strftime("%d %b %Y")

    market_data = get_market_data()

    prompt = f"""คุณเป็น AI Trading Analyst ของช่อง TradingTP

ข้อมูลราคาตลาดล่าสุด (เทียบกับชั่วโมงก่อน):
{market_data}

วันที่: {today}

วิเคราะห์และเลือก 1 pair ที่มี setup ที่ดีที่สุดในวันนี้ แล้วสร้าง Daily Morning Signal ภาษาไทย
โดยให้ข้อมูลดังนี้:
1. Pair ที่เลือก และ Bias (Bullish / Bearish)
2. เหตุผลประกอบ 2-3 บรรทัด (กระชับ ชัดเจน)
3. Entry Zone, Take Profit, Stop Loss (เป็นตัวเลขราคา)
4. ระดับ Risk (Low / Medium / High)

ใช้ format นี้เท่านั้น:

📊 TradingTP Morning Signal — {today}

🔥 Pair: [PAIR]
📈 Bias: [Bullish/Bearish]

[เหตุผล 2-3 บรรทัด]

✅ Setup แนะนำ:
• [Buy/Sell] Zone: [ราคา]
• TP: [ราคา]
• SL: [ราคา]

⚠️ Risk: [Low/Medium/High]

───────────────
เทรดผ่าน IUX รับ spread ต่ำสุด
👉 สมัครฟรี: {affiliate_link}"""

    return _gemini(prompt, max_tokens=600)
