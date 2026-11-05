"""Persistence: an append-only domain event log plus a JSON read model per aggregate.

Tables
  domain_events  append-only (seq, aggregate_id, type, payload, activity, created_at)
  aggregates     current aggregate state (the projection the engine operates on)
  receivables    canonical receivable registry (one row per canonical ID - double financing guard)
  idempotency    API responses keyed by Idempotency-Key
  credit_profiles  one credit history per borrower (survives across invoices)
  settings       small key/value flags such as the compliance pause

Every mutation writes the new aggregate state and its domain events in one
transaction, so a crash can never leave state and log out of step.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, delete, insert, select, update

metadata = MetaData()
domain_events = Table(
    "domain_events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(64), nullable=False, unique=True),
    Column("aggregate_id", String(128), nullable=False, index=True),
    Column("type", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("activity", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
aggregates = Table(
    "aggregates",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("state", Text, nullable=False),
)
receivables = Table(
    "receivables",
    metadata,
    Column("canonical_id", String(80), primary_key=True),
    Column("aggregate_id", String(128), nullable=False),
)
idempotency = Table(
    "idempotency",
    metadata,
    Column("key", String(255), primary_key=True),
    Column("payload", Text, nullable=False),
)
credit_profiles = Table(
    "credit_profiles",
    metadata,
    Column("borrower_id", String(128), primary_key=True),
    Column("profile", Text, nullable=False),
)
settings = Table(
    "settings",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)


class ConcurrencyError(RuntimeError):
    pass


class Store:
    def __init__(self, database_url: str | None = None) -> None:
        url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./working_capital.db")
        connect_args = {"check_same_thread": False} if url.startswith("sqlite:") else {}
        self.engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        metadata.create_all(self.engine)
        self.lock = RLock()

    def close(self) -> None:
        self.engine.dispose()

    # ------------------------------------------------------------ aggregates
    def load(self, aggregate_id: str) -> tuple[dict[str, Any], int] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(aggregates.c.state, aggregates.c.version).where(aggregates.c.id == aggregate_id)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def ids(self) -> list[str]:
        with self.engine.connect() as conn:
            return [row[0] for row in conn.execute(select(aggregates.c.id))]

    def save(self, state: dict[str, Any], version: int, events: list[dict[str, Any]], activity: list[dict[str, Any]] | None = None) -> int:
        """Optimistic write: fails if another writer advanced the aggregate first."""
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            if version == 0:
                conn.execute(insert(aggregates).values(id=state["id"], version=1, state=json.dumps(state)))
            else:
                result = conn.execute(update(aggregates).where(aggregates.c.id == state["id"], aggregates.c.version == version).values(version=version + 1, state=json.dumps(state)))
                if result.rowcount != 1:
                    raise ConcurrencyError(f"Aggregate {state['id']} changed concurrently.")
            for event in events:
                conn.execute(insert(domain_events).values(event_id=str(uuid4()), aggregate_id=state["id"], type=event["type"], payload=event, activity=None, created_at=now))
            for entry in activity or []:
                conn.execute(insert(domain_events).values(event_id=entry["id"], aggregate_id=state["id"], type="activity", payload={}, activity=entry, created_at=now))
        return version + 1

    def delete_aggregate(self, aggregate_id: str) -> None:
        """Demo reset only. The event log is append-only, so a reset is itself logged."""
        with self.engine.begin() as conn:
            conn.execute(delete(aggregates).where(aggregates.c.id == aggregate_id))
            conn.execute(delete(receivables).where(receivables.c.aggregate_id == aggregate_id))
            conn.execute(insert(domain_events).values(event_id=str(uuid4()), aggregate_id=aggregate_id, type="demo.reset", payload={}, activity=None, created_at=datetime.now(timezone.utc)))
            conn.execute(delete(idempotency))

    # ------------------------------------------------------------ registry
    def register_receivable(self, canonical_id: str, aggregate_id: str) -> bool:
        with self.engine.begin() as conn:
            existing = conn.execute(select(receivables.c.aggregate_id).where(receivables.c.canonical_id == canonical_id)).fetchone()
            if existing:
                return existing[0] == aggregate_id
            conn.execute(insert(receivables).values(canonical_id=canonical_id, aggregate_id=aggregate_id))
            return True

    # ------------------------------------------------------------ events
    def activity(self, aggregate_id: str, limit: int = 30, since_seq: int = 0) -> list[dict[str, Any]]:
        """Activity for the current run (entries after the latest demo reset)."""
        with self.engine.connect() as conn:
            last_reset = conn.execute(
                select(domain_events.c.seq).where(domain_events.c.aggregate_id == aggregate_id, domain_events.c.type == "demo.reset").order_by(domain_events.c.seq.desc()).limit(1)
            ).scalar()
            since_seq = max(since_seq, last_reset or 0)
            rows = conn.execute(
                select(domain_events.c.seq, domain_events.c.activity)
                .where(domain_events.c.aggregate_id == aggregate_id, domain_events.c.type == "activity", domain_events.c.seq > since_seq)
                .order_by(domain_events.c.seq.desc())
                .limit(limit)
            ).fetchall()
        return [{**row[1], "seq": row[0]} for row in rows]

    def log(self, aggregate_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(domain_events.c.seq, domain_events.c.type, domain_events.c.payload, domain_events.c.created_at)
                .where(domain_events.c.aggregate_id == aggregate_id, domain_events.c.type != "activity")
                .order_by(domain_events.c.seq)
                .limit(limit)
            ).fetchall()
        return [{"seq": r[0], "type": r[1], "payload": r[2], "at": r[3].isoformat() if r[3] else None} for r in rows]

    # ------------------------------------------------------------ credit
    def profile(self, borrower_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(credit_profiles.c.profile).where(credit_profiles.c.borrower_id == borrower_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_profile(self, profile: dict[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(credit_profiles).where(credit_profiles.c.borrower_id == profile["borrowerId"]))
            conn.execute(insert(credit_profiles).values(borrower_id=profile["borrowerId"], profile=json.dumps(profile)))

    # ------------------------------------------------------------ settings
    def setting(self, key: str, default: str | None = None) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(settings.c.value).where(settings.c.key == key)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(settings).where(settings.c.key == key))
            conn.execute(insert(settings).values(key=key, value=value))

    # ------------------------------------------------------------ idempotency
    def idempotent(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(idempotency.c.payload).where(idempotency.c.key == key)).fetchone()
        return json.loads(row[0]) if row else None

    def remember(self, key: str | None, payload: dict[str, Any]) -> None:
        if not key:
            return
        with self.engine.begin() as conn:
            if conn.execute(select(idempotency.c.key).where(idempotency.c.key == key)).fetchone() is None:
                conn.execute(insert(idempotency).values(key=key, payload=json.dumps(payload)))
