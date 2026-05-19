import logging
import os
import time
from datetime import datetime

import requests
import pytz
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

PAIRS = {
    "XAUUSD": "GC=F",
    # "EURUSD": "EURUSD=X",
    # "GBPUSD": "GBPUSD=X",
    # "USDJPY": "JPY=X",
    # "BTCUSD": "BTC-USD",
}


def get_market_data() -> str:
    lines = []
    for name, ticker in PAIRS.items():
        try:
            df = yf.download(ticker, period="3d",
                             interval="1h", progress=False)
            if df.empty:
                continue
            close = df["Close"].dropna()
            latest = float(
                close.iloc[-1].iloc[0]) if hasattr(close.iloc[-1], 'iloc') else float(close.iloc[-1])
            prev = float(
                close.iloc[-2].iloc[0]) if hasattr(close.iloc[-2], 'iloc') else float(close.iloc[-2])
            change_pct = (latest - prev) / prev * 100
            sign = "+" if change_pct >= 0 else ""
            lines.append(f"  {name}: {latest:.4f}  ({sign}{change_pct:.2f}%)")
        except Exception:
            pass
    return "\n".join(lines) if lines else "ไม่สามารถดึงข้อมูลตลาดได้"


def _s(v):
    """แปลง Series element เป็น float อย่างปลอดภัย"""
    if hasattr(v, 'iloc'):
        v = v.iloc[0]
    return float(v)


def _rsi(close, period=14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return _s((100 - (100 / (1 + rs))).iloc[-1])


def get_gold_data() -> dict:
    """ดึงข้อมูล XAUUSD แบบละเอียด: H4, H1, M15 + DXY + US10Y"""
    result = {}
    try:
        df_1h = yf.download("GC=F", period="30d",
                            interval="1h", progress=False)
        df_15m = yf.download("GC=F", period="5d",
                             interval="15m", progress=False)
        df_1d = yf.download("GC=F", period="60d",
                            interval="1d", progress=False)

        # ── H1 indicators ────────────────────────────────────────────────────
        if not df_1h.empty:
            close = df_1h["Close"].dropna()
            high = df_1h["High"].dropna()
            low = df_1h["Low"].dropna()

            result["price"] = _s(close.iloc[-1])
            result["change_1h"] = (
                _s(close.iloc[-1]) - _s(close.iloc[-2])) / _s(close.iloc[-2]) * 100
            result["change_24h"] = (_s(close.iloc[-1]) - _s(close.iloc[-24])) / \
                _s(close.iloc[-24]) * 100 if len(close) >= 24 else 0

            result["ema20_1h"] = _s(close.ewm(span=20).mean().iloc[-1])
            result["ema50_1h"] = _s(close.ewm(span=50).mean().iloc[-1])
            result["ema200_1h"] = _s(close.ewm(span=200).mean().iloc[-1])
            result["rsi_1h"] = _rsi(close)

            # MACD (12, 26, 9)
            macd_line = close.ewm(span=12).mean() - close.ewm(span=26).mean()
            signal_line = macd_line.ewm(span=9).mean()
            hist = macd_line - signal_line
            result["macd_line"] = _s(macd_line.iloc[-1])
            result["macd_signal"] = _s(signal_line.iloc[-1])
            result["macd_hist"] = _s(hist.iloc[-1])
            result["macd_hist_prev"] = _s(hist.iloc[-2])

            # Bollinger Bands (20, 2)
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            result["bb_upper"] = _s(sma20.iloc[-1]) + 2 * _s(std20.iloc[-1])
            result["bb_middle"] = _s(sma20.iloc[-1])
            result["bb_lower"] = _s(sma20.iloc[-1]) - 2 * _s(std20.iloc[-1])

            result["high_1d"] = _s(high.tail(24).max())
            result["low_1d"] = _s(low.tail(24).min())
            result["high_5d"] = _s(high.tail(5 * 24).max())
            result["low_5d"] = _s(low.tail(5 * 24).min())

        # ── H4 (resample จาก H1) ─────────────────────────────────────────────
        if not df_1h.empty:
            df_4h = df_1h.resample("4h").agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
            ).dropna()
            close_4h = df_4h["Close"].dropna()
            result["ema20_4h"] = _s(close_4h.ewm(span=20).mean().iloc[-1])
            result["ema50_4h"] = _s(close_4h.ewm(span=50).mean().iloc[-1])
            result["rsi_4h"] = _rsi(close_4h)
            result["high_4h_recent"] = _s(df_4h["High"].iloc[-2])
            result["low_4h_recent"] = _s(df_4h["Low"].iloc[-2])

        # ── M15 (Confirmation) ───────────────────────────────────────────────
        if not df_15m.empty:
            close_15m = df_15m["Close"].dropna()
            result["rsi_15m"] = _rsi(close_15m)
            result["ema20_15m"] = _s(close_15m.ewm(span=20).mean().iloc[-1])
            result["price_15m"] = _s(close_15m.iloc[-1])

        # ── Daily ────────────────────────────────────────────────────────────
        if not df_1d.empty:
            close_1d = df_1d["Close"].dropna()
            result["ema20_1d"] = _s(close_1d.ewm(span=20).mean().iloc[-1])
            result["high_30d"] = _s(df_1d["High"].dropna().tail(30).max())
            result["low_30d"] = _s(df_1d["Low"].dropna().tail(30).min())

    except Exception:
        pass

    # ── DXY ──────────────────────────────────────────────────────────────────
    try:
        dxy = yf.download("DX-Y.NYB", period="3d",
                          interval="1h", progress=False)
        if not dxy.empty:
            dxy_c = dxy["Close"].dropna()
            result["dxy"] = _s(dxy_c.iloc[-1])
            result["dxy_change_1h"] = (
                _s(dxy_c.iloc[-1]) - _s(dxy_c.iloc[-2])) / _s(dxy_c.iloc[-2]) * 100
    except Exception:
        pass

    # ── US10Y Bond Yield ──────────────────────────────────────────────────────
    try:
        tnx = yf.download("^TNX", period="5d", interval="1h", progress=False)
        if not tnx.empty:
            tnx_c = tnx["Close"].dropna()
            result["us10y"] = _s(tnx_c.iloc[-1])
            result["us10y_change_24h"] = _s(
                tnx_c.iloc[-1]) - _s(tnx_c.iloc[-24]) if len(tnx_c) >= 24 else 0
    except Exception:
        pass

    return result


logger = logging.getLogger(__name__)

_MODEL = "gemini-flash-latest"  # Google AI Studio: "Gemini 3 Flash"


def _gemini(prompt: str, max_tokens: int = 800) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
        except requests.exceptions.Timeout:
            logger.warning("Gemini timeout attempt=%d", attempt)
            if attempt < 2:
                time.sleep(5)
            continue
        except requests.exceptions.RequestException as e:
            logger.warning("Gemini request error: %s", e)
            raise

        if resp.status_code in (429, 500, 502, 503, 504):
            logger.warning(
                "Gemini %d attempt=%d body=%.200s",
                resp.status_code, attempt, resp.text,
            )
            if attempt < 2:
                time.sleep(5)
            continue

        resp.raise_for_status()
        candidate = resp.json()["candidates"][0]
        text = candidate["content"]["parts"][0]["text"]
        finish = candidate.get("finishReason", "STOP")
        logger.info("Gemini OK len=%d finishReason=%s", len(text), finish)

        # MAX_TOKENS with very short text = Google silently throttling output.
        # Retry so the caller's 200-char guard can send a friendly busy message.
        if finish == "MAX_TOKENS" and len(text) < 200:
            logger.warning("Gemini MAX_TOKENS but only %d chars — treating as incomplete, retrying", len(text))
            if attempt < 2:
                time.sleep(5)
            continue

        return text

    raise RuntimeError(f"Gemini {_MODEL} returned incomplete output after 3 attempts")


def _get_session_info(now_bkk) -> str:
    hour = now_bkk.hour
    if 0 <= hour < 7:
        return "Asian Session (00:00–07:00) — Volatility ต่ำ ทองเคลื่อนไหวน้อย"
    elif 7 <= hour < 14:
        return "ช่วงรอ London Open — ตลาดยุโรปกำลังจะเปิด"
    elif 14 <= hour < 17:
        return "London Open (14:00–17:00) — Volatility สูง ระวังการ Breakout"
    elif 17 <= hour < 19:
        return "ช่วงระหว่าง Session — Volatility ลดลงชั่วคราว"
    elif 19 <= hour < 22:
        return "NY Open (19:00–22:00) — Volatility สูงสุด ข่าวสหรัฐมีผลมาก"
    else:
        return "ช่วง NY Late Session (22:00–00:00) — Volatility เริ่มลดลง"


def generate_gold_analysis() -> str:
    """วิเคราะห์ทองคำ (XAUUSD) ใน 5-10 ชั่วโมงข้างหน้า ตาม framework TradingTP"""
    affiliate_link = os.environ.get("IUX_AFFILIATE_LINK", "https://iux.com")

    bangkok = pytz.timezone("Asia/Bangkok")
    now = datetime.now(bangkok)
    now_str = now.strftime("%d %b %Y %H:%M")
    session_info = _get_session_info(now)

    data = get_gold_data()
    if not data:
        logger.warning("generate_gold_analysis: get_gold_data() returned empty — yfinance may be down")
        return "❌ ไม่สามารถดึงข้อมูลราคาทองได้ในขณะนี้ครับ"

    logger.info(
        "generate_gold_analysis data: price=%.2f rsi_1h=%.1f macd_hist=%.4f dxy=%.3f",
        data.get("price", 0), data.get("rsi_1h", 0),
        data.get("macd_hist", 0), data.get("dxy", 0),
    )

    def fmt_chg(v, decimals=2):
        return f"+{v:.{decimals}f}%" if v >= 0 else f"{v:.{decimals}f}%"

    macd_trend = "MACD บวก (Bullish momentum)" if data.get(
        "macd_hist", 0) > 0 else "MACD ลบ (Bearish momentum)"
    macd_accel = "Histogram ขยายตัว (momentum แรงขึ้น)" if abs(data.get("macd_hist", 0)) > abs(
        data.get("macd_hist_prev", 0)) else "Histogram หดตัว (momentum อ่อนลง)"
    price = data.get("price", 0)
    bb_pos = "ใกล้แนวต้าน Upper BB" if price > data.get("bb_middle", 0) + (data.get("bb_upper", 0) - data.get("bb_middle", 0)) * 0.7 else \
             "ใกล้แนวรับ Lower BB" if price < data.get("bb_middle", 0) - (data.get("bb_middle", 0) - data.get("bb_lower", 0)) * 0.7 else \
             "อยู่กลาง Bollinger Band"

    data_text = f"""
[ ราคาและการเปลี่ยนแปลง ]
ราคาปัจจุบัน : {price:.2f} USD
เปลี่ยน 1h   : {fmt_chg(data.get('change_1h', 0))}
เปลี่ยน 24h  : {fmt_chg(data.get('change_24h', 0))}

[ H4 — Trend หลัก ]
EMA20 (H4)  : {data.get('ema20_4h', 0):.2f}
EMA50 (H4)  : {data.get('ema50_4h', 0):.2f}
RSI14 (H4)  : {data.get('rsi_4h', 0):.1f}
High H4 ก่อน: {data.get('high_4h_recent', 0):.2f}
Low H4 ก่อน : {data.get('low_4h_recent', 0):.2f}

[ H1 — Entry Signal ]
EMA20 (H1)  : {data.get('ema20_1h', 0):.2f}
EMA50 (H1)  : {data.get('ema50_1h', 0):.2f}
EMA200 (H1) : {data.get('ema200_1h', 0):.2f}
RSI14 (H1)  : {data.get('rsi_1h', 0):.1f}
MACD Line   : {data.get('macd_line', 0):.2f}  Signal: {data.get('macd_signal', 0):.2f}  Hist: {data.get('macd_hist', 0):.2f}
  → {macd_trend} / {macd_accel}
BB Upper    : {data.get('bb_upper', 0):.2f}
BB Middle   : {data.get('bb_middle', 0):.2f}
BB Lower    : {data.get('bb_lower', 0):.2f}
  → ราคา{bb_pos}

[ M15 — Confirmation ]
RSI14 (M15) : {data.get('rsi_15m', 0):.1f}
EMA20 (M15) : {data.get('ema20_15m', 0):.2f}

[ Key Levels ]
High 1 วัน  : {data.get('high_1d', 0):.2f}   Low 1 วัน : {data.get('low_1d', 0):.2f}
High 5 วัน  : {data.get('high_5d', 0):.2f}   Low 5 วัน : {data.get('low_5d', 0):.2f}
High 30 วัน : {data.get('high_30d', 0):.2f}   Low 30 วัน: {data.get('low_30d', 0):.2f}
EMA20 Daily : {data.get('ema20_1d', 0):.2f}

[ Fundamental ]
DXY (Dollar Index): {data.get('dxy', 0):.3f}  ({fmt_chg(data.get('dxy_change_1h', 0))} /1h)
US10Y Bond Yield  : {data.get('us10y', 0):.3f}%  (เปลี่ยน 24h: {data.get('us10y_change_24h', 0):+.3f}%)

[ Session ปัจจุบัน ]
{session_info}
"""

    prompt = f"""คุณเป็น Senior AI Trading Analyst ของช่อง TradingTP เชี่ยวชาญด้านทองคำ (XAUUSD)

ข้อมูล ณ {now_str} (Bangkok Time):
{data_text}

ทำการวิเคราะห์ทิศทาง XAUUSD ใน 5-10 ชั่วโมงข้างหน้า โดยใช้ framework ดังนี้:

ขั้นตอนการวิเคราะห์ (ทำตามลำดับ):
1. ดู DXY + US10Y → กำหนด Fundamental Bias
2. ดู H4 (EMA + RSI) → หา Trend หลัก
3. ดู H1 (EMA, MACD, BB, RSI) → หาจังหวะเข้า
4. ดู M15 (RSI + EMA) → Confirmation
5. กำหนด Key Level S/R จาก High/Low 1d, 5d
6. คำนึงถึง Session ปัจจุบันว่า Volatility อยู่ระดับไหน

น้ำหนัก: Technical 60% + Fundamental 40%
ภาษาไทย กระชับ ชัดเจน ให้ข้อมูลที่ Trader นำไปใช้ได้จริง

ใช้ format นี้เท่านั้น:

🏅 XAUUSD Intraday Analysis — {now_str}
⏱ วิเคราะห์ช่วง 5-10 ชั่วโมงข้างหน้า

📡 Fundamental Bias:
[DXY และ Bond Yield บอกอะไร 1-2 บรรทัด]

📊 Technical Bias: [Bullish / Bearish / Sideways]
[H4 Trend อธิบาย 1 บรรทัด]
[H1 MACD + BB อธิบาย 1 บรรทัด]
[M15 Confirmation 1 บรรทัด]

🎯 Key Levels:
• แนวต้าน: [ราคา 1]  [ราคา 2]
• แนวรับ  : [ราคา 1]  [ราคา 2]

✅ Setup แนะนำ:
• [Buy/Sell] Zone: [ราคา]
• TP1: [ราคา]  TP2: [ราคา]
• SL: [ราคา]
• Risk/Reward: [1:X]

⚠️ ระวัง:
[Session + สิ่งที่ต้องระวัง 1-2 บรรทัด]

───────────────
เทรดผ่าน IUX รับ spread ต่ำสุด
👉 สมัครฟรี: {affiliate_link}

⚠️ เนื้อหานี้เป็นเพียงข้อมูลการวิเคราะห์จาก AI
การเทรดมีความเสี่ยง โปรดตัดสินใจด้วยตัวเองก่อนเข้าเทรด"""

    return _gemini(prompt, max_tokens=900)


def generate_signal() -> str:
    affiliate_link = os.environ.get("IUX_AFFILIATE_LINK", "https://iux.com")

    bangkok = pytz.timezone("Asia/Bangkok")
    today = datetime.now(bangkok).strftime("%d %b %Y")

    market_data = get_market_data()
    logger.info("generate_signal market_data=%r", market_data)

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
👉 สมัครฟรี: {affiliate_link}

⚠️ เนื้อหานี้เป็นเพียงข้อมูลการวิเคราะห์จาก AI
การเทรดมีความเสี่ยง โปรดตัดสินใจด้วยตัวเองก่อนเข้าเทรด"""

    return _gemini(prompt, max_tokens=600)
