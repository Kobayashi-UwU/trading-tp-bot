-- รัน SQL นี้ใน Supabase SQL Editor
-- https://supabase.com/dashboard → SQL Editor

CREATE TABLE IF NOT EXISTS users (
    line_user_id    TEXT PRIMARY KEY,
    iux_user_id     TEXT,
    pending_iux_id  TEXT,
    status          TEXT DEFAULT 'new',
    -- new | pending | verified | rejected | blocked
    state           TEXT DEFAULT 'waiting_iux',
    -- waiting_iux | confirming | done
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    verified_at     TIMESTAMPTZ
);

-- Index สำหรับ query by iux_user_id
CREATE INDEX IF NOT EXISTS idx_users_iux_user_id ON users(iux_user_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
