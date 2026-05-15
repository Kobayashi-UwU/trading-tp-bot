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

-- ─────────────────────────────────────────────────────────────────────────────
-- Explicit GRANTs (required from Oct 30, 2026 — Supabase Data API policy change)
-- Run ONLY after Oct 2026, or when fully ready to enforce RLS.
-- WARNING: enabling RLS without complete policies will block bot INSERTs.
-- ─────────────────────────────────────────────────────────────────────────────
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO service_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO authenticated;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO anon;
--
-- ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
--
-- CREATE POLICY IF NOT EXISTS "service_role full access"
--   ON public.users FOR ALL TO service_role
--   USING (true) WITH CHECK (true);
--
-- CREATE POLICY IF NOT EXISTS "anon full access"
--   ON public.users FOR ALL TO anon
--   USING (true) WITH CHECK (true);