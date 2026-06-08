-- ═══════════════════════════════════════════════════════════════
-- RLS HARDENING (safe / idempotent) — รันได้ใน Supabase SQL Editor
-- ───────────────────────────────────────────────────────────────
-- จุดประสงค์:
--   1. ปิดช่องโหว่ "Table publicly accessible" (เปิด Row-Level Security)
--   2. ตัดสิทธิ์ anon / authenticated (publishable key) ออกจากตาราง
--   3. ให้ service_role (secret key) มีสิทธิ์เต็มแบบ explicit
--      → กันผลกระทบจาก Supabase Data API change (30 Oct 2026)
--
-- ⚠️ PRE-REQUISITE สำคัญ (ห้ามข้าม):
--   ต้องเปลี่ยน SUPABASE_KEY ใน Railway เป็น "secret key" (sb_secret_...)
--   และ deploy ให้บอทใช้ key ใหม่เรียบร้อย "ก่อน" รัน SQL นี้
--   มิฉะนั้นบอทที่ยังใช้ publishable key (sb_publishable_...) จะถูกบล็อกทันที
--
--   secret key  (sb_secret_...)      → map เป็น role service_role → bypass RLS
--   publishable key (sb_publishable) → map เป็น role anon         → ถูก RLS บล็อก
-- ═══════════════════════════════════════════════════════════════

-- 1. เปิด Row-Level Security บนตาราง users
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- (ตัวเลือกเสริม) บังคับ RLS แม้กับ table owner เพื่อความเข้มงวด
-- ALTER TABLE public.users FORCE ROW LEVEL SECURITY;

-- 2. ตัดสิทธิ์ทั้งหมดที่ anon / authenticated เคยมี
--    (publishable key / client key จะเข้าถึงตารางไม่ได้อีก)
REVOKE ALL ON public.users FROM anon;
REVOKE ALL ON public.users FROM authenticated;

-- 3. ให้ service_role มีสิทธิ์เต็มแบบ explicit
--    (service_role bypass RLS อยู่แล้ว แต่เขียนชัดเจนเพื่อกัน
--     default-grant change ของ Data API ที่จะ enforce 30 Oct 2026)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO service_role;

-- ───────────────────────────────────────────────────────────────
-- VERIFICATION — รันเพื่อตรวจผล
-- ───────────────────────────────────────────────────────────────

-- 3.1 ต้องเห็น rowsecurity = true
SELECT schemaname, tablename, rowsecurity
FROM   pg_tables
WHERE  schemaname = 'public' AND tablename = 'users';

-- 3.2 ตรวจ grants ปัจจุบัน — ควรเหลือเฉพาะ service_role (และ owner/postgres)
--     ไม่ควรมี anon / authenticated อยู่ในผลลัพธ์อีก
SELECT grantee, privilege_type
FROM   information_schema.role_table_grants
WHERE  table_schema = 'public' AND table_name = 'users'
ORDER  BY grantee, privilege_type;
