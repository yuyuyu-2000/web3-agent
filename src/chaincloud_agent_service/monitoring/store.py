from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from chaincloud_agent_service.monitoring.models import MonitorRule, NotificationEvent

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid SQL identifier: {value!r}")
    return value


def _rule(row: dict[str, Any]) -> MonitorRule:
    return MonitorRule(**{key: row.get(key) for key in MonitorRule.__dataclass_fields__})


class MonitorStore:
    """Short-connection PostgreSQL repository shared by tools and the worker."""

    def __init__(self, database_url: str, *, prefix: str = "monitor") -> None:
        self.database_url = database_url
        self.rules = _identifier(f"{prefix}_rules")
        self.events = _identifier(f"{prefix}_notification_events")
        self.state = _identifier(f"{prefix}_scan_state")
        self.configs = _identifier(f"{prefix}_notification_configs")

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    @contextmanager
    def scan_lock(self):
        """Cross-process guard: only one application instance scans at a time."""
        with self.connect() as conn:
            acquired = conn.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                (f"chaincloud:{self.rules}:scan",),
            ).fetchone()["acquired"]
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    conn.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        (f"chaincloud:{self.rules}:scan",),
                    )

    def ensure_schema(self) -> None:
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.rules} (
            rule_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            rule_type TEXT NOT NULL CHECK (rule_type IN ('address_transaction','large_transaction')),
            address TEXT, min_amount NUMERIC, min_amount_usd NUMERIC,
            chain TEXT, token TEXT, enabled BOOLEAN NOT NULL DEFAULT TRUE,
            notification_channel TEXT NOT NULL DEFAULT 'feishu',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_triggered_at TIMESTAMPTZ,
            CHECK (address IS NOT NULL OR min_amount IS NOT NULL OR min_amount_usd IS NOT NULL)
        );
        CREATE INDEX IF NOT EXISTS idx_{self.rules}_user ON {self.rules}(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_{self.rules}_enabled ON {self.rules}(enabled) WHERE enabled;
        CREATE TABLE IF NOT EXISTS {self.events} (
            event_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL REFERENCES {self.rules}(rule_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL, transaction_id TEXT NOT NULL, transaction_hash TEXT NOT NULL,
            channel TEXT NOT NULL, payload JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ, UNIQUE(rule_id, transaction_id)
        );
        CREATE INDEX IF NOT EXISTS idx_{self.events}_pending ON {self.events}(status, created_at);
        CREATE TABLE IF NOT EXISTS {self.state} (
            worker_name TEXT PRIMARY KEY, last_processed_id TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS {self.configs} (
            user_id TEXT NOT NULL, channel TEXT NOT NULL, destination TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(user_id, channel)
        );
        """
        with self.connect() as conn:
            conn.execute(sql)

    def create_rule(self, *, user_id: str, rule_type: str, address: str | None = None,
                    min_amount: Decimal | None = None, min_amount_usd: Decimal | None = None,
                    chain: str | None = None, token: str | None = None,
                    notification_channel: str = "feishu") -> MonitorRule:
        if rule_type not in {"address_transaction", "large_transaction"}:
            raise ValueError("rule_type must be address_transaction or large_transaction")
        if rule_type == "address_transaction" and not address:
            raise ValueError("address_transaction requires address")
        if rule_type == "large_transaction" and min_amount is None and min_amount_usd is None:
            raise ValueError("large_transaction requires min_amount or min_amount_usd")
        rule_id = str(uuid.uuid4())
        query = f"""INSERT INTO {self.rules}
        (rule_id,user_id,rule_type,address,min_amount,min_amount_usd,chain,token,notification_channel)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *"""
        with self.connect() as conn:
            row = conn.execute(query, (rule_id, user_id, rule_type, address.lower() if address else None,
                               min_amount, min_amount_usd, chain.lower() if chain else None,
                               token.lower() if token else None, notification_channel)).fetchone()
        return _rule(row)

    def list_rules(self, user_id: str) -> list[MonitorRule]:
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM {self.rules} WHERE user_id=%s ORDER BY created_at DESC", (user_id,)).fetchall()
        return [_rule(row) for row in rows]

    def list_enabled_rules(self) -> list[MonitorRule]:
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM {self.rules} WHERE enabled=TRUE").fetchall()
        return [_rule(row) for row in rows]

    def delete_rule(self, user_id: str, rule_id: str) -> bool:
        with self.connect() as conn:
            result = conn.execute(f"DELETE FROM {self.rules} WHERE rule_id=%s AND user_id=%s", (rule_id, user_id))
        return result.rowcount > 0

    def set_enabled(self, user_id: str, rule_id: str, enabled: bool) -> bool:
        with self.connect() as conn:
            result = conn.execute(f"UPDATE {self.rules} SET enabled=%s WHERE rule_id=%s AND user_id=%s", (enabled, rule_id, user_id))
        return result.rowcount > 0

    def cursor(self, worker_name: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(f"SELECT last_processed_id FROM {self.state} WHERE worker_name=%s", (worker_name,)).fetchone()
        return row["last_processed_id"] if row else None

    def persist_matches_and_cursor(self, worker_name: str, cursor: str, matches: list[dict[str, Any]]) -> int:
        inserted = 0
        with self.connect() as conn:
            for match in matches:
                result = conn.execute(f"""INSERT INTO {self.events}
                    (event_id,rule_id,user_id,transaction_id,transaction_hash,channel,payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(rule_id,transaction_id) DO NOTHING""",
                    (str(uuid.uuid4()), match["rule_id"], match["user_id"], match["transaction_id"],
                     match["transaction_hash"], match["channel"], Jsonb(match["payload"])))
                inserted += result.rowcount
                if result.rowcount:
                    conn.execute(f"UPDATE {self.rules} SET last_triggered_at=now() WHERE rule_id=%s", (match["rule_id"],))
            conn.execute(f"""INSERT INTO {self.state}(worker_name,last_processed_id) VALUES (%s,%s)
                ON CONFLICT(worker_name) DO UPDATE SET last_processed_id=EXCLUDED.last_processed_id, updated_at=now()""",
                (worker_name, cursor))
        return inserted

    def pending_events(self, limit: int = 100) -> list[NotificationEvent]:
        with self.connect() as conn:
            rows = conn.execute(f"SELECT event_id,rule_id,user_id,transaction_id,transaction_hash,channel,payload,status,attempts FROM {self.events} WHERE status IN ('pending','failed') AND attempts < 5 ORDER BY created_at LIMIT %s", (limit,)).fetchall()
        return [NotificationEvent(**row) for row in rows]

    def mark_sent(self, event_id: str) -> None:
        with self.connect() as conn:
            conn.execute(f"UPDATE {self.events} SET status='sent', attempts=attempts+1, sent_at=now(), last_error=NULL WHERE event_id=%s", (event_id,))

    def mark_failed(self, event_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(f"UPDATE {self.events} SET status='failed', attempts=attempts+1, last_error=%s WHERE event_id=%s", (error[:1000], event_id))

    def set_notification_destination(self, user_id: str, channel: str, destination: str) -> None:
        with self.connect() as conn:
            conn.execute(f"""INSERT INTO {self.configs}(user_id,channel,destination) VALUES (%s,%s,%s)
                ON CONFLICT(user_id,channel) DO UPDATE SET destination=EXCLUDED.destination, enabled=TRUE, updated_at=now()""",
                (user_id, channel, destination))

    def notification_destination(self, user_id: str, channel: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(f"SELECT destination FROM {self.configs} WHERE user_id=%s AND channel=%s AND enabled=TRUE", (user_id, channel)).fetchone()
        return str(row["destination"]) if row else None
