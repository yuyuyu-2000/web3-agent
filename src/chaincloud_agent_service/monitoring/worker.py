from __future__ import annotations

import logging
import re
import time
from contextlib import nullcontext
from decimal import Decimal
from typing import Any, Callable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from chaincloud_agent_service.monitoring.models import MonitorRule, TransactionRecord
from chaincloud_agent_service.monitoring.store import MonitorStore
from chaincloud_agent_service.notification.service import NotificationService

logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EMPTY_CURSOR = "__monitor_empty__"


class PostgresTransactionSource:
    """One keyset-paginated query per scan, independent of rule count."""

    def __init__(self, database_url: str, *, table: str, columns: dict[str, str], batch_size: int = 1000,
                 process_existing_on_first_run: bool = False) -> None:
        self.database_url, self.table = database_url, table
        self.columns, self.batch_size = columns, batch_size
        self.process_existing = process_existing_on_first_run
        for value in [table, *columns.values()]:
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"invalid transaction identifier: {value!r}")

    def scan(self, cursor: str | None) -> tuple[list[TransactionRecord], str | None]:
        id_col = self.columns["id"]
        initialized_empty = cursor == _EMPTY_CURSOR
        if initialized_empty:
            cursor = None
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            if cursor is None and not initialized_empty and not self.process_existing:
                row = conn.execute(sql.SQL("SELECT MAX({}) AS cursor FROM {}").format(
                    sql.Identifier(id_col), sql.Identifier(self.table))).fetchone()
                return [], str(row["cursor"]) if row and row["cursor"] is not None else _EMPTY_CURSOR
            selected = {name: column for name, column in self.columns.items()}
            projection = sql.SQL(",").join(
                sql.SQL("{} AS {}").format(sql.Identifier(column), sql.Identifier(name))
                for name, column in selected.items()
            )
            where = sql.SQL("") if cursor is None else sql.SQL("WHERE {} > %s").format(sql.Identifier(id_col))
            query = sql.SQL("SELECT {} FROM {} {} ORDER BY {} ASC LIMIT %s").format(
                projection, sql.Identifier(self.table), where, sql.Identifier(id_col))
            params = (self.batch_size,) if cursor is None else (cursor, self.batch_size)
            rows = conn.execute(query, params).fetchall()
        txs = [self._transaction(row) for row in rows]
        return txs, (txs[-1].transaction_id if txs else cursor)

    @staticmethod
    def _transaction(row: dict[str, Any]) -> TransactionRecord:
        def dec(value: Any) -> Decimal | None:
            return Decimal(str(value)) if value is not None else None
        return TransactionRecord(
            transaction_id=str(row["id"]), transaction_hash=str(row["hash"]),
            from_address=str(row["from_address"]).lower() if row.get("from_address") else None,
            to_address=str(row["to_address"]).lower() if row.get("to_address") else None,
            amount=dec(row.get("amount")), amount_usd=dec(row.get("amount_usd")),
            chain=str(row["chain"]).lower() if row.get("chain") else None,
            token=str(row["token"]).lower() if row.get("token") else None,
            occurred_at=row.get("occurred_at"), raw=dict(row),
        )


def matches(rule: MonitorRule, tx: TransactionRecord) -> bool:
    if rule.chain and rule.chain.lower() != tx.chain:
        return False
    if rule.token and rule.token.lower() != tx.token:
        return False
    if rule.address and rule.address.lower() not in {tx.from_address, tx.to_address}:
        return False
    if rule.min_amount is not None and (tx.amount is None or tx.amount < rule.min_amount):
        return False
    if rule.min_amount_usd is not None and (tx.amount_usd is None or tx.amount_usd < rule.min_amount_usd):
        return False
    return True


class MonitorWorker:
    def __init__(self, store: MonitorStore, source: PostgresTransactionSource,
                 notifications: NotificationService, destination_for_user: Callable[[str, str], str | None],
                 *, worker_name: str = "transactions") -> None:
        self.store, self.source, self.notifications = store, source, notifications
        self.destination_for_user, self.worker_name = destination_for_user, worker_name

    def run_once(self) -> dict[str, Any]:
        started = time.perf_counter()
        metrics = {"event": "monitor_scan", "new_transactions": 0, "enabled_rules": 0,
                   "matched_rules": 0, "notification_success": 0, "notification_failure": 0, "error": None}
        logger.info("monitor scan started worker=%s", self.worker_name)
        try:
            lock = self.store.scan_lock() if hasattr(self.store, "scan_lock") else nullcontext(True)
            with lock as acquired:
                if not acquired:
                    metrics["skipped"] = "another_worker_holds_lock"
                else:
                    self._scan_and_notify(metrics)
        except Exception as exc:
            metrics["error"] = exc.__class__.__name__
            logger.exception("monitor scan failed worker=%s", self.worker_name)
        metrics["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        logger.info("monitor scan completed metrics=%s", metrics)
        return metrics

    def _scan_and_notify(self, metrics: dict[str, Any]) -> None:
        rules = self.store.list_enabled_rules()
        transactions, cursor = self.source.scan(self.store.cursor(self.worker_name))
        metrics.update(new_transactions=len(transactions), enabled_rules=len(rules))
        matches_to_store: list[dict[str, Any]] = []
        for tx in transactions:
            for rule in rules:
                if matches(rule, tx):
                    payload = {"rule_id": rule.rule_id, "address": rule.address,
                               "transaction_hash": tx.transaction_hash, "amount": str(tx.amount) if tx.amount is not None else None,
                               "amount_usd": str(tx.amount_usd) if tx.amount_usd is not None else None,
                               "chain": tx.chain, "token": tx.token,
                               "triggered_at": tx.occurred_at.isoformat() if tx.occurred_at else None}
                    matches_to_store.append({"rule_id": rule.rule_id, "user_id": rule.user_id,
                        "transaction_id": tx.transaction_id, "transaction_hash": tx.transaction_hash,
                        "channel": rule.notification_channel, "payload": payload})
        if cursor is not None:
            metrics["matched_rules"] = self.store.persist_matches_and_cursor(self.worker_name, cursor, matches_to_store)
        for event in self.store.pending_events():
            destination = self.destination_for_user(event.user_id, event.channel)
            result = self.notifications.send(event.channel, destination, event.payload)
            if result.success:
                self.store.mark_sent(event.event_id); metrics["notification_success"] += 1
            else:
                self.store.mark_failed(event.event_id, result.error or "unknown error"); metrics["notification_failure"] += 1
