import logging
import os
import time
from datetime import datetime

import requests
import pytz
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Asset configuration
# ---------------------------------------------------------------------------

ASSETS: dict[str, dict] = {
    "XAUUSD": {"ticker": "GC=F",     "name": "Gold vs US Dollar",                "emoji": "🏅", "type": "commodity",  "decimals": 2},
    "BTCUSD": {"ticker": "BTC-USD",  "name": "Bitcoin vs US Dollar (CFD)",       "emoji": "₿",  "type": "crypto",     "decimals": 2},
    "EURUSD": {"ticker": "EURUSD=X", "name": "Euro vs US Dollar",                "emoji": "💱", "type": "forex_usd",  "decimals": 5},
    "USDJPY": {"ticker": "JPY=X",    "name": "US Dollar vs Japanese Yen",        "emoji": "💱", "type": "forex_usd",  "decimals": 3},
    "GBPUSD": {"ticker": "GBPUSD=X", "name": "Great Britain Pound vs US Dollar", "emoji": "💱", "type": "forex_usd",  "decimals": 5},
    "AUDUSD": {"ticker": "AUDUSD=X", "name": "Australian Dollar vs US Dollar",   "emoji": "💱", "type": "forex_usd",  "decimals": 5},
    "GBPJPY": {"ticker": "GBPJPY=X", "name": "GBP vs Japanese Yen",              "emoji": "💱", "type": "forex_cross","decimals": 3},
    "ETHUSD": {"ticker": "ETH-USD",  "name": "Ethereum vs US Dollar (CFD)",      "emoji": "⟠",  "type": "crypto",     "decimals": 2},
    "EURJPY": {"ticker": "EURJPY=X", "name": "Euro vs Japanese Yen",             "emoji": "💱", "type": "forex_cross","decimals": 3},
    "USOIL":  {"ticker": "CL=F",     "name": "Crude Oil WTI",                    "emoji": "🛢", "type": "commodity",  "decimals": 2},
    "USDCAD": {"ticker": "CAD=X",    "name": "US Dollar vs Canadian Dollar",     "emoji": "💱", "type": "forex_usd",  "decimals": 5},
    "ADAUSD": {"ticker": "ADA-USD",  "name": "Cardano vs US Dollar (CFD)",       "emoji": "🔵", "type": "crypto",     "decimals": 5},
    "EURAUD": {"ticker": "EURAUD=X", "name": "Euro vs Australian Dollar",        "emoji": "💱", "type": "forex_cross","decimals": 5},
    "USDCHF": {"ticker": "CHF=X",    "name": "US Dollar vs Swiss Franc",         "emoji": "💱", "type": "forex_usd",  "decimals": 5},
    "EURGBP": {"ticker": "EURGBP=X", "name": "Euro vs Great Britain Pound",      "emoji": "💱", "type": "forex_cross","decimals": 5},
    "NZDUSD": {"ticker": "NZDUSD=X", "name": "New Zealand Dollar vs US Dollar",  "emoji": "💱", "type": "forex_usd",  "decimals": 5},
    "SOLUSD": {"ticker": "SOL-USD",  "name": "Solana vs US Dollar (CFD)",        "emoji": "◎",  "type": "crypto",     "decimals": 3},
    "AUDJPY": {"ticker": "AUDJPY=X", "name": "Australian Dollar vs Japanese Yen","emoji": "💱", "type": "forex_cross","decimals": 3},
}

# จำนวนครั้งที่ user ขอ /signal ได้ต่อวัน (รีเซ็ตทุกวันตามเวลาไทย)
DAILY_SIGNAL_LIMIT = 3

SIGNAL_HELP = (
    "📊 วิธีดู Daily Signal:\n"
    "พิมพ์ /signal [asset] เพื่อขอ signal (วันละ 3 ครั้ง)\n"
    "ไม่ระบุ asset → ได้ XAUUSD (ทองคำ) โดยอัตโนมัติ\n\n"
    "ตัวอย่าง:\n"
    "• /signal          → XAUUSD (ทองคำ)\n"
    "• /signal BTCUSD   → Bitcoin\n"
    "• /signal EURUSD   → EUR/USD\n\n"
    "Asset ที่รองรับ:\n"
    "🏅 XAUUSD  🛢 USOIL\n"
    "💱 EURUSD  USDJPY  GBPUSD  AUDUSD  USDCAD  USDCHF  NZDUSD\n"
    "   GBPJPY  EURJPY  EURAUD  EURGBP  AUDJPY\n"
    "₿  BTCUSD  ETHUSD  ADAUSD  SOLUSD"
)

_ASSET_LIST = "  ".join(sorted(ASSETS.keys()))


def parse_signal_asset(text: str) -> tuple:
    """Parse '/signal [ASSET]' command text.

    Returns (symbol, error_msg):
      - Valid command + known asset  → ("BTCUSD", None)
      - Valid command + no asset     → ("XAUUSD", None)  [default]
      - Valid command + unknown asset→ (None, "<error message>")
      - Not a /signal command        → (None, None)
    """
    parts = text.strip().split()
    if not parts or parts[0].lower() != "/signal":
        return None, None
    if len(parts) == 1:
        return "XAUUSD", None
    symbol = parts[1].upper()
    if symbol not in ASSETS:
        return None, (
            f"❌ ไม่รู้จัก asset '{parts[1]}' ครับ\n\n"
            f"Asset ที่รองรับ:\n{_ASSET_LIST}"
        )
    return symbol, None


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _s(v):
    if hasattr(v, "iloc"):
        v = v.iloc[0]
    return float(v)


def _rsi(close, period=14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return _s((100 - (100 / (1 + rs))).iloc[-1])


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def get_asset_data(symbol: str) -> dict:
    """Download OHLCV and compute technical indicators for any supported asset.

    Also fetches DXY for commodity/forex_usd assets, and US10Y for XAUUSD only.
    """
    cfg = ASSETS[symbol]
    ticker = cfg["ticker"]
    asset_type = cfg["type"]
    result = {}

    try:
        df_1h = yf.download(ticker, period="30d",  interval="1h",  progress=False)
        df_15m = yf.download(ticker, period="5d",   interval="15m", progress=False)
        df_1d  = yf.download(ticker, period="60d",  interval="1d",  progress=False)

        if not df_1h.empty:
            close = df_1h["Close"].dropna()
            high  = df_1h["High"].dropna()
            low   = df_1h["Low"].dropna()

            result["price"]      = _s(close.iloc[-1])
            result["change_1h"]  = (_s(close.iloc[-1]) - _s(close.iloc[-2])) / _s(close.iloc[-2]) * 100
            result["change_24h"] = (_s(close.iloc[-1]) - _s(close.iloc[-24])) / _s(close.iloc[-24]) * 100 if len(close) >= 24 else 0

            result["ema20_1h"]  = _s(close.ewm(span=20).mean().iloc[-1])
            result["ema50_1h"]  = _s(close.ewm(span=50).mean().iloc[-1])
            result["ema200_1h"] = _s(close.ewm(span=200).mean().iloc[-1])
            result["rsi_1h"]    = _rsi(close)

            macd_line   = close.ewm(span=12).mean() - close.ewm(span=26).mean()
            signal_line = macd_line.ewm(span=9).mean()
            hist        = macd_line - signal_line
            result["macd_line"]      = _s(macd_line.iloc[-1])
            result["macd_signal"]    = _s(signal_line.iloc[-1])
            result["macd_hist"]      = _s(hist.iloc[-1])
            result["macd_hist_prev"] = _s(hist.iloc[-2])

            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            result["bb_upper"]  = _s(sma20.iloc[-1]) + 2 * _s(std20.iloc[-1])
            result["bb_middle"] = _s(sma20.iloc[-1])
            result["bb_lower"]  = _s(sma20.iloc[-1]) - 2 * _s(std20.iloc[-1])

            result["high_1d"] = _s(high.tail(24).max())
            result["low_1d"]  = _s(low.tail(24).min())
            result["high_5d"] = _s(high.tail(5 * 24).max())
            result["low_5d"]  = _s(low.tail(5 * 24).min())

            # H4 (resampled from H1)
            df_4h    = df_1h.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
            close_4h = df_4h["Close"].dropna()
            result["ema20_4h"]      = _s(close_4h.ewm(span=20).mean().iloc[-1])
            result["ema50_4h"]      = _s(close_4h.ewm(span=50).mean().iloc[-1])
            result["rsi_4h"]        = _rsi(close_4h)
            result["high_4h_recent"] = _s(df_4h["High"].iloc[-2])
            result["low_4h_recent"]  = _s(df_4h["Low"].iloc[-2])

        if not df_15m.empty:
            close_15m = df_15m["Close"].dropna()
            result["rsi_15m"]    = _rsi(close_15m)
            result["ema20_15m"]  = _s(close_15m.ewm(span=20).mean().iloc[-1])
            result["price_15m"]  = _s(close_15m.iloc[-1])

        if not df_1d.empty:
            close_1d = df_1d["Close"].dropna()
            result["ema20_1d"]  = _s(close_1d.ewm(span=20).mean().iloc[-1])
            result["high_30d"]  = _s(df_1d["High"].dropna().tail(30).max())
            result["low_30d"]   = _s(df_1d["Low"].dropna().tail(30).min())

    except Exception:
        pass

    # DXY — relevant for USD-denominated assets (commodity + forex_usd)
    if asset_type in ("commodity", "forex_usd"):
        try:
            dxy = yf.download("DX-Y.NYB", period="3d", interval="1h", progress=False)
            if not dxy.empty:
                dxy_c = dxy["Close"].dropna()
                result["dxy"]          = _s(dxy_c.iloc[-1])
                result["dxy_change_1h"] = (_s(dxy_c.iloc[-1]) - _s(dxy_c.iloc[-2])) / _s(dxy_c.iloc[-2]) * 100
        except Exception:
            pass

    # US10Y Bond Yield — primary driver for gold only
    if symbol == "XAUUSD":
        try:
            tnx = yf.download("^TNX", period="5d", interval="1h", progress=False)
            if not tnx.empty:
                tnx_c = tnx["Close"].dropna()
                result["us10y"]             = _s(tnx_c.iloc[-1])
                result["us10y_change_24h"]  = _s(tnx_c.iloc[-1]) - _s(tnx_c.iloc[-24]) if len(tnx_c) >= 24 else 0
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Gemini API
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_MODELS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
]


def _try_gemini_once(model, payload, headers):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.RequestException as e:
        logger.warning("Gemini %s request error: %s", model, e)
        return None, "error"

    if resp.status_code == 429:
        logger.warning("Gemini %s quota exhausted (429): %.200s", model, resp.text)
        return None, "quota"
    if resp.status_code in (500, 502, 503, 504):
        logger.warning("Gemini %s %d: %.200s", model, resp.status_code, resp.text)
        return None, "overload"
    if not resp.ok:
        logger.warning("Gemini %s %d: %.200s", model, resp.status_code, resp.text)
        return None, "error"

    candidate = resp.json()["candidates"][0]
    text = candidate.get("content", {}).get("parts", [{}])[0].get("text", "")
    finish = candidate.get("finishReason", "STOP")

    if finish == "MAX_TOKENS" and len(text) < 200:
        logger.warning("Gemini %s MAX_TOKENS but only %d chars", model, len(text))
        return None, "short"

    logger.info("Gemini OK model=%s len=%d finish=%s", model, len(text), finish)
    return text, "ok"


def _gemini(prompt: str, max_tokens: int = 6000) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}

    for model in _MODELS:
        for attempt in range(3):
            text, status = _try_gemini_once(model, payload, headers)
            if status == "ok":
                return text
            if status in ("quota", "error"):
                break
            if attempt < 2:
                time.sleep(3)

    raise RuntimeError("Gemini ทุก model ใช้งานไม่ได้ (quota หมดหรือ overload)")


# ---------------------------------------------------------------------------
# Session info
# ---------------------------------------------------------------------------

def _get_session_info(now_bkk, asset_type: str = "commodity") -> str:
    hour = now_bkk.hour
    if asset_type == "crypto":
        if 0 <= hour < 7:
            return "Asian Hours (00:00–07:00 BKK) — Crypto Volatility ต่ำกว่าปกติ"
        elif 7 <= hour < 14:
            return "Pre-London Hours — Volatility เริ่มเพิ่มขึ้น"
        elif 14 <= hour < 22:
            return "London + NY Hours (14:00–22:00) — Crypto Volatility สูงขึ้น ระวัง news-driven moves"
        else:
            return "NY Late Session (22:00–00:00) — Volatility เริ่มลดลง"

    if 0 <= hour < 7:
        return "Asian Session (00:00–07:00) — Volatility ต่ำ เคลื่อนไหวน้อย"
    elif 7 <= hour < 14:
        return "ช่วงรอ London Open — ตลาดยุโรปกำลังจะเปิด"
    elif 14 <= hour < 17:
        return "London Open (14:00–17:00) — Volatility สูง ระวังการ Breakout"
    elif 17 <= hour < 19:
        return "ช่วงระหว่าง Session — Volatility ลดลงชั่วคราว"
    elif 19 <= hour < 22:
        return "NY Open (19:00–22:00) — Volatility สูงสุด"
    else:
        return "ช่วง NY Late Session (22:00–00:00) — Volatility เริ่มลดลง"


# ---------------------------------------------------------------------------
# Prompt helpers (per asset type)
# ---------------------------------------------------------------------------

def _expert_role(symbol: str, cfg: dict) -> str:
    t = cfg["type"]
    if symbol == "XAUUSD":
        return "เชี่ยวชาญด้านทองคำ (XAUUSD)"
    elif symbol == "USOIL":
        return "เชี่ยวชาญด้านตลาดน้ำมัน (WTI Crude Oil)"
    elif t in ("forex_usd", "forex_cross"):
        return f"เชี่ยวชาญด้านตลาด Forex ({symbol})"
    elif t == "crypto":
        return f"เชี่ยวชาญด้าน Cryptocurrency ({symbol})"
    return f"เชี่ยวชาญด้านตลาด ({symbol})"


def _fundamental_step(symbol: str, asset_type: str) -> str:
    if symbol == "XAUUSD":
        return "1. ดู DXY + US10Y → กำหนด Fundamental Bias"
    elif symbol == "USOIL":
        return "1. ดู DXY → กำหนด USD Bias (น้ำมัน inverse correlation กับ USD)"
    elif asset_type == "forex_usd":
        return "1. ดู DXY → กำหนด USD Dollar Strength Bias"
    elif asset_type == "forex_cross":
        return "1. ประเมิน Sentiment ของทั้งสองสกุลเงินจาก momentum และ session ที่เปิดอยู่"
    else:  # crypto
        return "1. ประเมิน Risk Sentiment จาก momentum และ session (Crypto เปิด 24/7)"


def _weight_text(asset_type: str) -> str:
    if asset_type == "crypto":
        return "Technical 80% + Sentiment 20%"
    elif asset_type == "forex_cross":
        return "Technical 80% + Session Analysis 20%"
    elif asset_type == "forex_usd":
        return "Technical 70% + Fundamental (DXY) 30%"
    else:
        return "Technical 60% + Fundamental 40%"


def _fundamental_data_section(data: dict, symbol: str, asset_type: str, fmt_chg) -> str:
    lines = []
    if "dxy" in data:
        lines.append(f"DXY (Dollar Index): {data['dxy']:.3f}  ({fmt_chg(data.get('dxy_change_1h', 0))} /1h)")
    if "us10y" in data:
        lines.append(f"US10Y Bond Yield  : {data['us10y']:.3f}%  (เปลี่ยน 24h: {data.get('us10y_change_24h', 0):+.3f}%)")
    if not lines:
        if asset_type == "crypto":
            return "[ Fundamental ]\nCrypto market — วิเคราะห์จาก Technical + Sentiment เป็นหลัก ไม่มี DXY/Bond Yield"
        elif asset_type == "forex_cross":
            return "[ Fundamental ]\nForex Cross — ไม่ขึ้นตรงกับ DXY วิเคราะห์ทั้งสองสกุลเงิน"
        return "[ Fundamental ]\n(ไม่มีข้อมูล Fundamental)"
    return "[ Fundamental ]\n" + "\n".join(lines)


def _fundamental_bias_hint(symbol: str, asset_type: str) -> str:
    if symbol == "XAUUSD":
        return "[DXY และ Bond Yield บอกอะไร 1-2 บรรทัด]"
    elif asset_type in ("commodity", "forex_usd"):
        return "[DXY บอกอะไรเกี่ยวกับ USD Strength 1-2 บรรทัด]"
    elif asset_type == "forex_cross":
        return "[Sentiment ของทั้งสองสกุลเงิน และ session ที่มีผล 1-2 บรรทัด]"
    else:
        return "[Risk sentiment ของตลาด Crypto และ momentum 1-2 บรรทัด]"


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def generate_analysis(symbol: str) -> str:
    """Analyse any supported asset and return a Thai-language trading signal."""
    symbol = symbol.upper()
    if symbol not in ASSETS:
        return f"❌ ไม่รู้จัก asset '{symbol}' ครับ"

    cfg = ASSETS[symbol]
    asset_type = cfg["type"]
    d = cfg["decimals"]
    affiliate_link = os.environ.get("IUX_AFFILIATE_LINK", "https://iux.com")

    bangkok = pytz.timezone("Asia/Bangkok")
    now = datetime.now(bangkok)
    now_str = now.strftime("%d %b %Y %H:%M")
    session_info = _get_session_info(now, asset_type)

    data = get_asset_data(symbol)
    if not data:
        logger.warning("generate_analysis(%s): get_asset_data() returned empty", symbol)
        return f"❌ ไม่สามารถดึงข้อมูลราคา {symbol} ได้ในขณะนี้ครับ"

    logger.info(
        "generate_analysis(%s): price=%.{d}f rsi_1h=%.1f macd_hist=%.{d}f".format(d=d),
        symbol, data.get("price", 0), data.get("rsi_1h", 0), data.get("macd_hist", 0),
    )

    def fmt_chg(v, decimals=2):
        return f"+{v:.{decimals}f}%" if v >= 0 else f"{v:.{decimals}f}%"

    macd_trend = "MACD บวก (Bullish momentum)" if data.get("macd_hist", 0) > 0 else "MACD ลบ (Bearish momentum)"
    macd_accel = (
        "Histogram ขยายตัว (momentum แรงขึ้น)"
        if abs(data.get("macd_hist", 0)) > abs(data.get("macd_hist_prev", 0))
        else "Histogram หดตัว (momentum อ่อนลง)"
    )
    price = data.get("price", 0)
    bb_pos = (
        "ใกล้แนวต้าน Upper BB"
        if price > data.get("bb_middle", 0) + (data.get("bb_upper", 0) - data.get("bb_middle", 0)) * 0.7
        else "ใกล้แนวรับ Lower BB"
        if price < data.get("bb_middle", 0) - (data.get("bb_middle", 0) - data.get("bb_lower", 0)) * 0.7
        else "อยู่กลาง Bollinger Band"
    )

    fundamental_section = _fundamental_data_section(data, symbol, asset_type, fmt_chg)

    data_text = f"""
[ ราคาและการเปลี่ยนแปลง ]
ราคาปัจจุบัน : {price:.{d}f}
เปลี่ยน 1h   : {fmt_chg(data.get('change_1h', 0))}
เปลี่ยน 24h  : {fmt_chg(data.get('change_24h', 0))}

[ H4 — Trend หลัก ]
EMA20 (H4)  : {data.get('ema20_4h', 0):.{d}f}
EMA50 (H4)  : {data.get('ema50_4h', 0):.{d}f}
RSI14 (H4)  : {data.get('rsi_4h', 0):.1f}
High H4 ก่อน: {data.get('high_4h_recent', 0):.{d}f}
Low H4 ก่อน : {data.get('low_4h_recent', 0):.{d}f}

[ H1 — Entry Signal ]
EMA20 (H1)  : {data.get('ema20_1h', 0):.{d}f}
EMA50 (H1)  : {data.get('ema50_1h', 0):.{d}f}
EMA200 (H1) : {data.get('ema200_1h', 0):.{d}f}
RSI14 (H1)  : {data.get('rsi_1h', 0):.1f}
MACD Line   : {data.get('macd_line', 0):.{d}f}  Signal: {data.get('macd_signal', 0):.{d}f}  Hist: {data.get('macd_hist', 0):.{d}f}
  → {macd_trend} / {macd_accel}
BB Upper    : {data.get('bb_upper', 0):.{d}f}
BB Middle   : {data.get('bb_middle', 0):.{d}f}
BB Lower    : {data.get('bb_lower', 0):.{d}f}
  → ราคา{bb_pos}

[ M15 — Confirmation ]
RSI14 (M15) : {data.get('rsi_15m', 0):.1f}
EMA20 (M15) : {data.get('ema20_15m', 0):.{d}f}

[ Key Levels ]
High 1 วัน  : {data.get('high_1d', 0):.{d}f}   Low 1 วัน : {data.get('low_1d', 0):.{d}f}
High 5 วัน  : {data.get('high_5d', 0):.{d}f}   Low 5 วัน : {data.get('low_5d', 0):.{d}f}
High 30 วัน : {data.get('high_30d', 0):.{d}f}   Low 30 วัน: {data.get('low_30d', 0):.{d}f}
EMA20 Daily : {data.get('ema20_1d', 0):.{d}f}

{fundamental_section}

[ Session ปัจจุบัน ]
{session_info}
"""

    prompt = f"""คุณเป็น Senior AI Trading Analyst ของช่อง TradingTP {_expert_role(symbol, cfg)}

ข้อมูล ณ {now_str} (Bangkok Time):
{data_text}

ทำการวิเคราะห์ทิศทาง {symbol} ใน 5-10 ชั่วโมงข้างหน้า โดยใช้ framework ดังนี้:

ขั้นตอนการวิเคราะห์ (ทำตามลำดับ):
{_fundamental_step(symbol, asset_type)}
2. ดู H4 (EMA + RSI) → หา Trend หลัก
3. ดู H1 (EMA, MACD, BB, RSI) → หาจังหวะเข้า
4. ดู M15 (RSI + EMA) → Confirmation
5. กำหนด Key Level S/R จาก High/Low 1d, 5d
6. คำนึงถึง Session ปัจจุบันว่า Volatility อยู่ระดับไหน

น้ำหนัก: {_weight_text(asset_type)}
ภาษาไทย กระชับ ชัดเจน ให้ข้อมูลที่ Trader นำไปใช้ได้จริง

ใช้ format นี้เท่านั้น:

{cfg['emoji']} {symbol} Intraday Analysis — {now_str}
⏱ วิเคราะห์ช่วง 5-10 ชั่วโมงข้างหน้า

📡 Fundamental Bias:
{_fundamental_bias_hint(symbol, asset_type)}

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

    return _gemini(prompt, max_tokens=6000)


def generate_gold_analysis() -> str:
    """Backward-compatible wrapper — analyse XAUUSD."""
    return generate_analysis("XAUUSD")
