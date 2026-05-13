import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


class Database:
    def __init__(self):
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self.client: Client = create_client(url, key)
        self.table = "users"

    # ------------------------------------------------------------------
    # Single-user lookups
    # ------------------------------------------------------------------

    def get_user(self, user_id: str, platform: str = "line") -> dict | None:
        result = (
            self.client.table(self.table)
            .select("*")
            .eq("user_id", user_id)
            .eq("platform", platform)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_user_by_iux_id(self, iux_user_id: str) -> dict | None:
        """Return the first row matching the IUX ID (any platform)."""
        result = (
            self.client.table(self.table)
            .select("*")
            .eq("iux_user_id", iux_user_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_all_users_by_iux_id(self, iux_user_id: str) -> list[dict]:
        """Return every row matching the IUX ID across all platforms."""
        result = (
            self.client.table(self.table)
            .select("*")
            .eq("iux_user_id", iux_user_id)
            .execute()
        )
        return result.data

    # ------------------------------------------------------------------
    # Bulk lookups
    # ------------------------------------------------------------------

    def get_all_users(self) -> list[dict]:
        result = self.client.table(self.table).select("*").execute()
        return result.data

    def get_verified_users(self) -> list[dict]:
        """Return user_id, platform, and notification_token for all verified users."""
        result = (
            self.client.table(self.table)
            .select("user_id, platform, notification_token")
            .eq("status", "verified")
            .execute()
        )
        return result.data

    def get_admin_users(self) -> list[dict]:
        """Return all users with user_role = 'admin' across all platforms."""
        result = (
            self.client.table(self.table)
            .select("user_id, platform, display_name")
            .eq("user_role", "admin")
            .execute()
        )
        return result.data

    def get_long_pending_users(self, hours: int = 1) -> list[dict]:
        """Return pending users waiting longer than `hours` who haven't been reminded yet."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) -
                  timedelta(hours=hours)).isoformat()
        result = (
            self.client.table(self.table)
            .select("*")
            .eq("status", "pending")
            .eq("reminder_sent", False)
            .lt("created_at", cutoff)
            .execute()
        )
        return result.data

    def search_by_name(self, keyword: str) -> list[dict]:
        result = (
            self.client.table(self.table)
            .select("*")
            .ilike("display_name", f"%{keyword}%")
            .execute()
        )
        return result.data

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert_user(self, user_id: str, platform: str = "line", **kwargs) -> None:
        if kwargs.get("status") == "verified":
            kwargs["verified_at"] = datetime.now(timezone.utc).isoformat()
        data = {"user_id": user_id, "platform": platform, **kwargs}
        self.client.table(self.table).upsert(data).execute()

    def update_iux_id(self, user_id: str, new_iux_id: str, platform: str = "line") -> None:
        self.client.table(self.table).upsert({
            "user_id": user_id,
            "platform": platform,
            "iux_user_id": new_iux_id,
            "pending_iux_id": None,
        }).execute()

    def reset_user(self, user_id: str, platform: str = "line") -> None:
        self.client.table(self.table).upsert({
            "user_id": user_id,
            "platform": platform,
            "iux_user_id": None,
            "pending_iux_id": None,
            "status": "new",
            "state": "waiting_iux",
        }).execute()
