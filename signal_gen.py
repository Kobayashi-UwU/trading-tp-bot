import os
from datetime import datetime

import anthropic
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
            latest = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            change_pct = (latest - prev) / prev * 100
            sign = "+" if change_pct >= 0 else ""
            lines.append(f"  {name}: {latest:.4f}  ({sign}{change_pct:.2f}%)")
        except Exception:
            pass
    return "\n".join(lines) if lines else "ไม่สามารถดึงข้อมูลตลาดได้"


def generate_signal() -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
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

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
