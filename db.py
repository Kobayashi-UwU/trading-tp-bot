import os
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


class Database:
    def __init__(self):
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self.client: Client = create_client(url, key)
        self.table = "users"

    def get_user(self, line_user_id: str) -> dict | None:
        result = (
            self.client.table(self.table)
            .select("*")
            .eq("line_user_id", line_user_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_user_by_iux_id(self, iux_user_id: str) -> dict | None:
        result = (
            self.client.table(self.table)
            .select("*")
            .eq("iux_user_id", iux_user_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_all_users(self) -> list[dict]:
        result = self.client.table(self.table).select("*").execute()
        return result.data

    def get_verified_users(self) -> list[dict]:
        result = (
            self.client.table(self.table)
            .select("line_user_id")
            .eq("status", "verified")
            .execute()
        )
        return result.data

    def upsert_user(self, line_user_id: str, **kwargs) -> None:
        if "status" in kwargs and kwargs["status"] == "verified":
            kwargs["verified_at"] = datetime.now(timezone.utc).isoformat()
        data = {"line_user_id": line_user_id, **kwargs}
        self.client.table(self.table).upsert(data).execute()

    def reset_user(self, line_user_id: str) -> None:
        self.client.table(self.table).upsert({
            "line_user_id": line_user_id,
            "iux_user_id": None,
            "pending_iux_id": None,
            "status": "new",
            "state": "waiting_iux",
        }).execute()
