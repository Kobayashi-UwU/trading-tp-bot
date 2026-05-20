-- ═══════════════════════════════════════════════════════════════
-- FRESH INSTALL — รันบน DB ใหม่ที่ยังไม่มีตาราง
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS users (
    user_id             TEXT        NOT NULL,
    platform            TEXT        NOT NULL DEFAULT 'line',
    -- platform: 'line' | 'facebook'
    iux_user_id         TEXT,
    pending_iux_id      TEXT,
    display_name        TEXT,
    status              TEXT        DEFAULT 'new',
    -- new | pending | verified | rejected | blocked
    state               TEXT        DEFAULT 'waiting_iux',
    -- waiting_iux | confirming | done
    pending_notified    BOOLEAN     DEFAULT FALSE,
    notification_token  TEXT,
    -- Facebook Recurring Notifications token (FB only)
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    verified_at         TIMESTAMPTZ,
    PRIMARY KEY (platform, user_id)
);

CREATE INDEX IF NOT EXISTS idx_users_iux_user_id ON users(iux_user_id);
CREATE INDEX IF NOT EXISTS idx_users_status      ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_platform    ON users(platform);


-- ═══════════════════════════════════════════════════════════════
-- MIGRATION — รันบน DB เดิมที่มี line_user_id เป็น PK อยู่แล้ว
-- (ข้ามส่วนนี้ถ้าเพิ่ง setup ใหม่)
-- ═══════════════════════════════════════════════════════════════

-- 1. เพิ่ม columns ใหม่ (ต้องรันก่อน step อื่น)
ALTER TABLE users ADD COLUMN IF NOT EXISTS platform           TEXT NOT NULL DEFAULT 'line';
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name       TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_notified   BOOLEAN DEFAULT FALSE;

-- 2. สร้าง index สำหรับ platform (ต้องรันหลัง ADD COLUMN platform)
ALTER TABLE users ADD COLUMN IF NOT EXISTS reminder_sent      BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS user_role          TEXT DEFAULT NULL;
-- user_role: NULL = user ปกติ, 'admin' = admin
CREATE INDEX IF NOT EXISTS idx_users_platform ON users(platform);

-- 3. เปลี่ยนชื่อ column (รัน 1 ครั้งเท่านั้น — comment ออกหลังรันแล้ว)
ALTER TABLE users RENAME COLUMN line_user_id TO user_id;

-- 4. เปลี่ยน Primary Key เป็น composite (platform, user_id)
--    (รัน 1 ครั้งเท่านั้น หลังจาก rename เสร็จแล้ว)
ALTER TABLE users DROP CONSTRAINT users_pkey;
ALTER TABLE users ADD PRIMARY KEY (platform, user_id);

ALTER TABLE users ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS user_role TEXT DEFAULT NULL;

-- ═══════════════════════════════════════════════════════════════
-- RLS HARDENING — รันหลังเปลี่ยน SUPABASE_KEY ใน Railway เป็น
-- service_role key เรียบร้อยแล้ว
-- ═══════════════════════════════════════════════════════════════
-- WARNING: ห้ามรัน SQL ด้านล่างถ้าบอทยังใช้ anon key อยู่ —
-- บอทจะ INSERT/SELECT ตารางไม่ได้ทันทีและพังหมด
--
-- service_role key bypass RLS โดย default แม้ไม่มี policy
-- anon / authenticated จะถูกบล็อกทั้งหมดเพราะไม่มี policy ให้
-- ═══════════════════════════════════════════════════════════════

-- 1. เปิด Row-Level Security
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- 2. ตัดสิทธิ์ที่ anon / authenticated เคยมีออกให้หมด
REVOKE ALL ON public.users FROM anon;
REVOKE ALL ON public.users FROM authenticated;

-- 3. ให้ service_role มีสิทธิ์เต็ม (จริงๆ bypass RLS อยู่แล้ว
--    แต่เขียนชัดเจนเพื่อกัน Supabase Data API policy เปลี่ยน Oct 2026)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO service_role;

-- 4. ตรวจผล — ควรเห็น rowsecurity = true
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public' AND tablename = 'users';