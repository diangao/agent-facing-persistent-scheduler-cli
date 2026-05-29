from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import uuid

from .timeparse import format_dt, parse_datetime, parse_duration, utc_now


DEFAULT_HOME = Path.home() / ".agent-scheduler"
DEFAULT_DB = DEFAULT_HOME / "scheduler.sqlite3"
MISSED_GRACE_SECONDS = 60


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    namespace: str | None
    target: str | None
    schedule_kind: str
    next_fire_at: datetime
    payload: dict
    enabled: bool
    interval_seconds: int | None
    created_at: datetime
    updated_at: datetime


class SchedulerStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init()

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.executescript(
            """
            create table if not exists rules (
              id text primary key,
              title text not null,
              namespace text,
              target text,
              schedule_kind text not null,
              next_fire_at text not null,
              payload_json text not null,
              enabled integer not null default 1,
              interval_seconds integer,
              created_at text not null,
              updated_at text not null
            );

            create table if not exists runs (
              id text primary key,
              rule_id text not null,
              scheduled_for text not null,
              fired_at text not null,
              status text not null,
              event_json text not null,
              error text,
              foreign key(rule_id) references rules(id)
            );
            """
        )
        self._ensure_rule_metadata_columns()
        self.conn.commit()

    def _ensure_rule_metadata_columns(self) -> None:
        existing = {row["name"] for row in self.conn.execute("pragma table_info(rules)")}
        if "namespace" not in existing:
            self.conn.execute("alter table rules add column namespace text")
        if "target" not in existing:
            self.conn.execute("alter table rules add column target text")

    def create_rule(
        self,
        *,
        title: str,
        next_fire_at: datetime,
        payload: dict,
        interval: str | None = None,
        namespace: str | None = None,
        target: str | None = None,
    ) -> Rule:
        now = utc_now()
        rule_id = "r_" + uuid.uuid4().hex[:12]
        interval_seconds = None
        schedule_kind = "one-shot"
        if interval:
            interval_seconds = int(parse_duration(interval).total_seconds())
            schedule_kind = "interval"
        self.conn.execute(
            """
            insert into rules (
              id, title, namespace, target, schedule_kind, next_fire_at, payload_json, enabled,
              interval_seconds, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                rule_id,
                title,
                _normalize_optional_string(namespace),
                _normalize_optional_string(target),
                schedule_kind,
                format_dt(next_fire_at),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                interval_seconds,
                format_dt(now),
                format_dt(now),
            ),
        )
        self.conn.commit()
        return self.get_rule(rule_id)

    def list_rules(self, *, include_disabled: bool = False) -> list[Rule]:
        query = "select * from rules"
        params: tuple[object, ...] = ()
        if not include_disabled:
            query += " where enabled = 1"
        query += " order by next_fire_at asc"
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def get_rule(self, rule_id: str) -> Rule:
        row = self.conn.execute("select * from rules where id = ?", (rule_id,)).fetchone()
        if row is None:
            raise KeyError(f"rule not found: {rule_id}")
        return self._row_to_rule(row)

    def cancel_rule(self, rule_id: str) -> Rule:
        now = utc_now()
        cursor = self.conn.execute(
            "update rules set enabled = 0, updated_at = ? where id = ?",
            (format_dt(now), rule_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"rule not found: {rule_id}")
        self.conn.commit()
        return self.get_rule(rule_id)

    def update_rule(
        self,
        rule_id: str,
        *,
        title: str | None = None,
        next_fire_at: datetime | None = None,
        payload: dict | None = None,
        interval: str | None = None,
        namespace: str | None = None,
        target: str | None = None,
    ) -> Rule:
        self.get_rule(rule_id)
        fields: list[str] = []
        params: list[object] = []
        if title is not None:
            fields.append("title = ?")
            params.append(title)
        if next_fire_at is not None:
            fields.append("next_fire_at = ?")
            params.append(format_dt(next_fire_at))
            fields.append("enabled = 1")
        if payload is not None:
            fields.append("payload_json = ?")
            params.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        if interval is not None:
            fields.append("schedule_kind = 'interval'")
            fields.append("interval_seconds = ?")
            params.append(int(parse_duration(interval).total_seconds()))
        if namespace is not None:
            fields.append("namespace = ?")
            params.append(_normalize_optional_string(namespace))
        if target is not None:
            fields.append("target = ?")
            params.append(_normalize_optional_string(target))
        if not fields:
            return self.get_rule(rule_id)
        fields.append("updated_at = ?")
        params.append(format_dt(utc_now()))
        params.append(rule_id)
        self.conn.execute(
            f"update rules set {', '.join(fields)} where id = ?",
            tuple(params),
        )
        self.conn.commit()
        return self.get_rule(rule_id)

    def snooze_rule(self, rule_id: str, *, next_fire_at: datetime) -> Rule:
        if next_fire_at <= utc_now():
            raise ValueError("snooze target must be in the future")
        self.get_rule(rule_id)
        now = utc_now()
        self.conn.execute(
            "update rules set next_fire_at = ?, enabled = 1, updated_at = ? where id = ?",
            (format_dt(next_fire_at), format_dt(now), rule_id),
        )
        self.conn.commit()
        return self.get_rule(rule_id)

    def due_rules(self, *, now: datetime | None = None, limit: int | None = None) -> list[Rule]:
        cutoff = format_dt(now or utc_now())
        query = "select * from rules where enabled = 1 and next_fire_at <= ? order by next_fire_at asc"
        params: list[object] = [cutoff]
        if limit is not None:
            query += " limit ?"
            params.append(limit)
        rows = self.conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def fire_rule(self, rule: Rule, *, fired_at: datetime | None = None) -> dict:
        fired = fired_at or utc_now()
        run_id = "run_" + uuid.uuid4().hex[:12]
        event = {
            "type": "scheduler.fire",
            "rule_id": rule.id,
            "run_id": run_id,
            "title": rule.title,
            "scheduled_for": format_dt(rule.next_fire_at),
            "fired_at": format_dt(fired),
            "payload": rule.payload,
        }
        if rule.namespace is not None:
            event["namespace"] = rule.namespace
        if rule.target is not None:
            event["target"] = rule.target
        self.conn.execute(
            """
            insert into runs (id, rule_id, scheduled_for, fired_at, status, event_json)
            values (?, ?, ?, ?, 'emitted', ?)
            """,
            (
                run_id,
                rule.id,
                format_dt(rule.next_fire_at),
                format_dt(fired),
                json.dumps(event, ensure_ascii=False, sort_keys=True),
            ),
        )
        if rule.schedule_kind == "interval" and rule.interval_seconds:
            next_fire = _next_future_fire(rule, after=fired)
            self.conn.execute(
                "update rules set next_fire_at = ?, updated_at = ? where id = ?",
                (format_dt(next_fire), format_dt(fired), rule.id),
            )
        else:
            self.conn.execute(
                "update rules set enabled = 0, updated_at = ? where id = ?",
                (format_dt(fired), rule.id),
            )
        self.conn.commit()
        return event

    def emit_due_rule(
        self,
        rule: Rule,
        *,
        observed_at: datetime | None = None,
        missed_grace_seconds: int = MISSED_GRACE_SECONDS,
    ) -> dict:
        observed = observed_at or utc_now()
        missed_by = int((observed - rule.next_fire_at).total_seconds())
        if missed_by > missed_grace_seconds:
            return self.miss_rule(rule, detected_at=observed, missed_by_seconds=missed_by)
        return self.fire_rule(rule, fired_at=observed)

    def miss_rule(
        self,
        rule: Rule,
        *,
        detected_at: datetime | None = None,
        missed_by_seconds: int | None = None,
    ) -> dict:
        detected = detected_at or utc_now()
        missed_by = missed_by_seconds
        if missed_by is None:
            missed_by = int((detected - rule.next_fire_at).total_seconds())
        run_id = "run_" + uuid.uuid4().hex[:12]
        event = {
            "type": "scheduler.missed",
            "rule_id": rule.id,
            "title": rule.title,
            "scheduled_for": format_dt(rule.next_fire_at),
            "detected_at": format_dt(detected),
            "missed_by_seconds": missed_by,
            "payload": rule.payload,
        }
        if rule.namespace is not None:
            event["namespace"] = rule.namespace
        if rule.target is not None:
            event["target"] = rule.target
        self.conn.execute(
            """
            insert into runs (id, rule_id, scheduled_for, fired_at, status, event_json)
            values (?, ?, ?, ?, 'missed', ?)
            """,
            (
                run_id,
                rule.id,
                format_dt(rule.next_fire_at),
                format_dt(detected),
                json.dumps(event, ensure_ascii=False, sort_keys=True),
            ),
        )
        if rule.schedule_kind == "interval" and rule.interval_seconds:
            next_fire = _next_future_fire(rule, after=detected)
            self.conn.execute(
                "update rules set next_fire_at = ?, updated_at = ? where id = ?",
                (format_dt(next_fire), format_dt(detected), rule.id),
            )
        else:
            self.conn.execute(
                "update rules set enabled = 0, updated_at = ? where id = ?",
                (format_dt(detected), rule.id),
            )
        self.conn.commit()
        return event

    def log(self, rule_id: str | None = None, *, limit: int = 50) -> list[dict]:
        query = "select * from runs"
        params: list[object] = []
        if rule_id:
            query += " where rule_id = ?"
            params.append(rule_id)
        query += " order by fired_at desc limit ?"
        params.append(limit)
        rows = self.conn.execute(query, tuple(params)).fetchall()
        return [
            {
                "id": row["id"],
                "rule_id": row["rule_id"],
                "scheduled_for": row["scheduled_for"],
                "fired_at": row["fired_at"],
                "status": row["status"],
                "event": json.loads(row["event_json"]),
                "error": row["error"],
            }
            for row in rows
        ]

    def _row_to_rule(self, row: sqlite3.Row) -> Rule:
        return Rule(
            id=row["id"],
            title=row["title"],
            namespace=row["namespace"],
            target=row["target"],
            schedule_kind=row["schedule_kind"],
            next_fire_at=parse_datetime(row["next_fire_at"]),
            payload=json.loads(row["payload_json"]),
            enabled=bool(row["enabled"]),
            interval_seconds=row["interval_seconds"],
            created_at=parse_datetime(row["created_at"]),
            updated_at=parse_datetime(row["updated_at"]),
        )


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _next_future_fire(rule: Rule, *, after: datetime) -> datetime:
    if rule.interval_seconds is None:
        raise ValueError("interval_seconds is required for recurring rules")
    next_fire = rule.next_fire_at
    interval = timedelta(seconds=rule.interval_seconds)
    while next_fire <= after:
        next_fire = next_fire + interval
    return next_fire
