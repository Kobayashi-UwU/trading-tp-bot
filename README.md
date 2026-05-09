# TradingTP LINE OA Signal Bot

Bot สำหรับส่ง Daily Trading Signal ให้ verified users ทุกเช้า 8:00 น. (Bangkok time)

## Setup

### 1. Supabase (Database)
1. สมัคร [supabase.com](https://supabase.com) ฟรี
2. สร้าง Project ใหม่
3. ไปที่ **SQL Editor** แล้วรัน `supabase_setup.sql`
4. Copy **Project URL** และ **anon key** จาก Project Settings → API

### 2. LINE Official Account
1. สมัคร LINE OA ที่ [manager.line.biz](https://manager.line.biz)
2. ไปที่ [developers.line.biz](https://developers.line.biz) → สร้าง Provider → Messaging API Channel
3. Copy **Channel Secret** และออก **Channel Access Token** (Long-lived)
4. หา **Admin LINE User ID** ของตัวเอง:
   - ไปที่ LINE Developers Console → Messaging API → Webhook
   - ส่งข้อความหา Bot ตัวเอง แล้วดู log เพื่อหา User ID
   - หรือใช้ [LINE User ID Finder](https://developers.line.biz/console/)

### 3. Anthropic API
1. สมัคร [console.anthropic.com](https://console.anthropic.com)
2. สร้าง API Key

### 4. Deploy บน Railway
1. Push code ขึ้น GitHub
2. สมัคร [railway.app](https://railway.app) ฟรี
3. New Project → Deploy from GitHub
4. ใส่ Environment Variables ทั้งหมดจาก `.env.example`
5. Railway จะให้ URL เช่น `https://yourapp.railway.app`

### 5. ตั้ง Webhook URL ใน LINE
- ไปที่ LINE Developers Console → Messaging API → Webhook URL
- ใส่: `https://yourapp.railway.app/webhook`
- กด **Verify** → ต้องขึ้น Success

---

## Admin Commands

ส่งคำสั่งเหล่านี้หา Bot ของตัวเอง:

| Command | ความหมาย |
|---|---|
| `/verify [IUX_ID]` | ยืนยัน user |
| `/reject [IUX_ID]` | ปฏิเสธ user |
| `/reset [IUX_ID]` | ให้ user ส่ง ID ใหม่ |
| `/list` | ดู users ทั้งหมด |
| `/signal` | generate signal ส่งให้ตัวเองทดสอบ |
| `/broadcast` | broadcast ไปหา verified users ทันที |
| `/help` | แสดง commands ทั้งหมด |

---

## User Flow

```
User add LINE OA
    → Bot ถาม IUX User ID
    → User ส่ง ID
    → Bot ยืนยัน (ใช่/ไม่)
    → Bot แจ้ง Admin รอ verify
    → Admin รัน /verify [ID]
    → User ได้รับ Signal ทุกเช้า 8:00
```
