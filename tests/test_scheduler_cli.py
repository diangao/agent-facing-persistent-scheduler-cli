from __future__ import annotations

from datetime import UTC, datetime
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_scheduler.cli import main


class SchedulerCliTest(unittest.TestCase):
    def run_cli(self, db: Path, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with patch("sys.stdout", output):
            code = main(["--db", str(db), *args])
        return code, output.getvalue()

    def test_one_shot_rule_fires_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "check draft",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"text":"check draft"}',
            )
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertTrue(rule["id"].startswith("r_"))
            self.assertIsNone(rule["namespace"])
            self.assertIsNone(rule["target"])

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:00:01Z")
            self.assertEqual(code, 0)
            events = [json.loads(line) for line in out.splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "scheduler.fire")
            self.assertEqual(events[0]["payload"], {"text": "check draft"})
            self.assertNotIn("namespace", events[0])
            self.assertNotIn("target", events[0])

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:00:02Z")
            self.assertEqual(code, 0)
            self.assertEqual(out, "")

    def test_interval_rule_reschedules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "heartbeat",
                "--at",
                "2026-05-29T20:00:00Z",
                "--every",
                "10m",
                "--payload",
                '{"kind":"heartbeat"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:00:01Z")
            self.assertEqual(code, 0)
            self.assertEqual(len(out.splitlines()), 1)

            code, out = self.run_cli(db, "show", rule_id)
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["next_fire_at"], "2026-05-29T20:10:00Z")
            self.assertTrue(rule["enabled"])

    def test_fire_now_emits_event_before_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "manual",
                "--at",
                "2026-05-30T20:00:00Z",
                "--payload",
                '{"kind":"manual"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, out = self.run_cli(db, "fire-now", rule_id)
            self.assertEqual(code, 0)
            event = json.loads(out)
            self.assertEqual(event["type"], "scheduler.fire")
            self.assertEqual(event["rule_id"], rule_id)

    def test_due_inside_grace_window_fires_not_missed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, _out = self.run_cli(
                db,
                "create",
                "--title",
                "slightly late",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"text":"still fire"}',
            )
            self.assertEqual(code, 0)

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:00:30Z")
            self.assertEqual(code, 0)
            event = json.loads(out)
            self.assertEqual(event["type"], "scheduler.fire")
            self.assertNotIn("missed_by_seconds", event)

    def test_missed_one_shot_emits_missed_and_disables_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "missed one-shot",
                "--namespace",
                "agent-runtime",
                "--target",
                "session:main",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"text":"missed"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:02:00Z")
            self.assertEqual(code, 0)
            event = json.loads(out)
            self.assertEqual(event["type"], "scheduler.missed")
            self.assertEqual(event["rule_id"], rule_id)
            self.assertEqual(event["scheduled_for"], "2026-05-29T20:00:00Z")
            self.assertEqual(event["detected_at"], "2026-05-29T20:02:00Z")
            self.assertEqual(event["missed_by_seconds"], 120)
            self.assertEqual(event["namespace"], "agent-runtime")
            self.assertEqual(event["target"], "session:main")
            self.assertNotIn("fired_at", event)

            code, out = self.run_cli(db, "show", rule_id)
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertFalse(rule["enabled"])

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:02:01Z")
            self.assertEqual(code, 0)
            self.assertEqual(out, "")

            code, out = self.run_cli(db, "log", rule_id)
            self.assertEqual(code, 0)
            logs = json.loads(out)
            self.assertEqual(logs[0]["status"], "missed")
            self.assertEqual(logs[0]["event"]["type"], "scheduler.missed")

    def test_missed_interval_emits_once_and_advances_to_future_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "missed interval",
                "--at",
                "2026-05-29T20:00:00Z",
                "--every",
                "10m",
                "--payload",
                '{"text":"missed interval"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-30T04:20:01Z")
            self.assertEqual(code, 0)
            events = [json.loads(line) for line in out.splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "scheduler.missed")
            self.assertEqual(events[0]["rule_id"], rule_id)
            self.assertEqual(events[0]["missed_by_seconds"], 30001)

            code, out = self.run_cli(db, "show", rule_id)
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertTrue(rule["enabled"])
            self.assertEqual(rule["next_fire_at"], "2026-05-30T04:30:00Z")

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-30T04:20:02Z")
            self.assertEqual(code, 0)
            self.assertEqual(out, "")

    def test_update_keeps_rule_id_and_run_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "old",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"text":"old"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]
            self.run_cli(db, "fire-now", rule_id)

            code, out = self.run_cli(
                db,
                "update",
                rule_id,
                "--title",
                "new",
                "--at",
                "2026-05-30T20:00:00Z",
                "--every",
                "30m",
                "--payload",
                '{"text":"new"}',
            )
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["id"], rule_id)
            self.assertEqual(rule["title"], "new")
            self.assertEqual(rule["schedule_kind"], "interval")
            self.assertEqual(rule["interval_seconds"], 1800)
            self.assertEqual(rule["next_fire_at"], "2026-05-30T20:00:00Z")
            self.assertEqual(rule["payload"], {"text": "new"})

            code, out = self.run_cli(db, "log", rule_id)
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out)), 1)

    def test_routing_metadata_round_trips_to_fire_event_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "routed",
                "--namespace",
                "claude-code",
                "--target",
                "session:main",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"text":"route me"}',
            )
            self.assertEqual(code, 0)
            rule = json.loads(out)
            rule_id = rule["id"]
            self.assertEqual(rule["namespace"], "claude-code")
            self.assertEqual(rule["target"], "session:main")

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:00:01Z")
            self.assertEqual(code, 0)
            event = json.loads(out)
            self.assertEqual(event["type"], "scheduler.fire")
            self.assertEqual(event["namespace"], "claude-code")
            self.assertEqual(event["target"], "session:main")

            code, out = self.run_cli(db, "log", rule_id)
            self.assertEqual(code, 0)
            logs = json.loads(out)
            self.assertEqual(logs[0]["event"]["namespace"], "claude-code")
            self.assertEqual(logs[0]["event"]["target"], "session:main")

    def test_update_can_set_and_clear_routing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "routing",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"text":"routing"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, out = self.run_cli(
                db,
                "update",
                rule_id,
                "--namespace",
                "codex",
                "--target",
                "repo:demo",
            )
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["namespace"], "codex")
            self.assertEqual(rule["target"], "repo:demo")

            code, out = self.run_cli(db, "update", rule_id, "--namespace", "", "--target", "")
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertIsNone(rule["namespace"])
            self.assertIsNone(rule["target"])

    def test_existing_store_without_routing_columns_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                create table rules (
                  id text primary key,
                  title text not null,
                  schedule_kind text not null,
                  next_fire_at text not null,
                  payload_json text not null,
                  enabled integer not null default 1,
                  interval_seconds integer,
                  created_at text not null,
                  updated_at text not null
                );
                insert into rules (
                  id, title, schedule_kind, next_fire_at, payload_json,
                  enabled, interval_seconds, created_at, updated_at
                ) values (
                  'r_old', 'old rule', 'one-shot', '2026-05-29T20:00:00Z',
                  '{"text":"old"}', 1, null,
                  '2026-05-29T19:00:00Z', '2026-05-29T19:00:00Z'
                );
                """
            )
            conn.commit()
            conn.close()

            code, out = self.run_cli(db, "show", "r_old")
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertIsNone(rule["namespace"])
            self.assertIsNone(rule["target"])

            code, out = self.run_cli(db, "update", "r_old", "--namespace", "legacy", "--target", "session:old")
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["namespace"], "legacy")
            self.assertEqual(rule["target"], "session:old")

    def test_update_noop_returns_existing_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "unchanged",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"text":"same"}',
            )
            self.assertEqual(code, 0)
            before = json.loads(out)

            code, out = self.run_cli(db, "update", before["id"])
            self.assertEqual(code, 0)
            after = json.loads(out)
            self.assertEqual(after, before)

    def test_snooze_one_shot_pushes_next_fire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "snooze me",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"kind":"one-shot"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, out = self.run_cli(db, "snooze", rule_id, "--until", "2099-05-29T21:00:00Z")
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["next_fire_at"], "2099-05-29T21:00:00Z")
            self.assertTrue(rule["enabled"])

    def test_snooze_recurring_only_changes_next_fire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "recurring",
                "--at",
                "2026-05-29T20:00:00Z",
                "--every",
                "10m",
                "--payload",
                '{"kind":"interval"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, out = self.run_cli(db, "snooze", rule_id, "--until", "2099-05-29T20:05:00Z")
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["next_fire_at"], "2099-05-29T20:05:00Z")
            self.assertEqual(rule["interval_seconds"], 600)

            code, out = self.run_cli(db, "run-due", "--now", "2099-05-29T20:05:01Z")
            self.assertEqual(code, 0)
            self.assertEqual(len(out.splitlines()), 1)
            code, out = self.run_cli(db, "show", rule_id)
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["next_fire_at"], "2099-05-29T20:15:00Z")

    def test_snooze_past_time_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, out = self.run_cli(
                db,
                "create",
                "--title",
                "past",
                "--at",
                "2026-05-29T20:00:00Z",
                "--payload",
                '{"kind":"past"}',
            )
            self.assertEqual(code, 0)
            rule_id = json.loads(out)["id"]

            code, _out = self.run_cli(db, "snooze", rule_id, "--until", "2000-01-01T00:00:00Z")
            self.assertEqual(code, 2)

    def test_random_daytime_rule_samples_inside_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            with patch(
                "agent_scheduler.store.utc_now",
                return_value=datetime(2026, 5, 29, 15, 0, tzinfo=UTC),
            ):
                code, out = self.run_cli(
                    db,
                    "create",
                    "--title",
                    "daytime check",
                    "--random-daytime",
                    "--window",
                    "09:00-17:00",
                    "--timezone",
                    "UTC",
                    "--count",
                    "2",
                    "--payload",
                    '{"kind":"proactive"}',
                )
            self.assertEqual(code, 0)
            rule = json.loads(out)
            self.assertEqual(rule["schedule_kind"], "random-daytime")
            self.assertEqual(rule["random_config"]["window_start"], "09:00")
            self.assertEqual(rule["random_config"]["window_end"], "17:00")
            self.assertEqual(rule["random_config"]["timezone"], "UTC")
            self.assertEqual(rule["random_config"]["count_min"], 2)
            self.assertEqual(rule["random_config"]["count_max"], 2)
            self.assertEqual(rule["random_config"]["count_per_day"], 2)
            next_fire = datetime.fromisoformat(
                rule["next_fire_at"].replace("Z", "+00:00")
            )
            self.assertGreaterEqual(next_fire, datetime(2026, 5, 29, 15, 0, tzinfo=UTC))
            self.assertLessEqual(next_fire, datetime(2026, 5, 29, 17, 0, tzinfo=UTC))

    def test_random_daytime_count_range_samples_daily_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            with (
                patch(
                    "agent_scheduler.store.utc_now",
                    return_value=datetime(2026, 5, 29, 8, 0, tzinfo=UTC),
                ),
                patch("agent_scheduler.store.random.randint", side_effect=[1, 0, 2, 0]),
            ):
                code, out = self.run_cli(
                    db,
                    "create",
                    "--title",
                    "variable daytime",
                    "--random-daytime",
                    "--window",
                    "09:00-17:00",
                    "--timezone",
                    "UTC",
                    "--count-range",
                    "1-2",
                    "--payload",
                    '{"kind":"proactive"}',
                )
                self.assertEqual(code, 0)
                rule = json.loads(out)
                self.assertEqual(rule["random_config"]["count_min"], 1)
                self.assertEqual(rule["random_config"]["count_max"], 2)
                self.assertEqual(rule["random_config"]["count_per_day"], 1)
                self.assertEqual(rule["next_fire_at"], "2026-05-29T09:00:00Z")

                code, _out = self.run_cli(db, "run-due", "--now", rule["next_fire_at"])
                self.assertEqual(code, 0)

            code, out = self.run_cli(db, "show", rule["id"])
            self.assertEqual(code, 0)
            updated = json.loads(out)
            self.assertEqual(updated["random_config"]["period_date"], "2026-05-30")
            self.assertEqual(updated["random_config"]["count_min"], 1)
            self.assertEqual(updated["random_config"]["count_max"], 2)
            self.assertEqual(updated["random_config"]["count_per_day"], 2)
            self.assertEqual(updated["next_fire_at"], "2026-05-30T09:00:00Z")

    def test_random_daytime_invalid_count_range_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            code, _out = self.run_cli(
                db,
                "create",
                "--title",
                "bad range",
                "--random-daytime",
                "--count-range",
                "3-2",
                "--payload",
                '{"kind":"proactive"}',
            )
            self.assertEqual(code, 2)

    def test_random_daytime_fire_resamples_future_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            with patch(
                "agent_scheduler.store.utc_now",
                return_value=datetime(2026, 5, 29, 8, 0, tzinfo=UTC),
            ):
                code, out = self.run_cli(
                    db,
                    "create",
                    "--title",
                    "daily random",
                    "--random-daytime",
                    "--window",
                    "09:00-17:00",
                    "--timezone",
                    "UTC",
                    "--count",
                    "1",
                    "--payload",
                    '{"kind":"proactive"}',
                )
            self.assertEqual(code, 0)
            rule = json.loads(out)
            rule_id = rule["id"]

            code, out = self.run_cli(
                db,
                "run-due",
                "--now",
                rule["next_fire_at"],
            )
            self.assertEqual(code, 0)
            event = json.loads(out)
            self.assertEqual(event["type"], "scheduler.fire")
            self.assertEqual(event["rule_id"], rule_id)

            code, out = self.run_cli(db, "show", rule_id)
            self.assertEqual(code, 0)
            updated = json.loads(out)
            self.assertTrue(updated["enabled"])
            self.assertEqual(updated["schedule_kind"], "random-daytime")
            self.assertGreater(updated["next_fire_at"], rule["next_fire_at"])

    def test_missed_random_daytime_emits_missed_and_resamples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "scheduler.sqlite3"
            with patch(
                "agent_scheduler.store.utc_now",
                return_value=datetime(2026, 5, 29, 8, 0, tzinfo=UTC),
            ):
                code, out = self.run_cli(
                    db,
                    "create",
                    "--title",
                    "missed random",
                    "--random-daytime",
                    "--window",
                    "09:00-17:00",
                    "--timezone",
                    "UTC",
                    "--count",
                    "1",
                    "--payload",
                    '{"kind":"proactive"}',
                )
            self.assertEqual(code, 0)
            rule = json.loads(out)
            rule_id = rule["id"]

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-30T20:00:00Z")
            self.assertEqual(code, 0)
            event = json.loads(out)
            self.assertEqual(event["type"], "scheduler.missed")
            self.assertEqual(event["rule_id"], rule_id)

            code, out = self.run_cli(db, "show", rule_id)
            self.assertEqual(code, 0)
            updated = json.loads(out)
            self.assertTrue(updated["enabled"])
            self.assertEqual(updated["schedule_kind"], "random-daytime")
            self.assertGreater(updated["next_fire_at"], "2026-05-30T20:00:00Z")


if __name__ == "__main__":
    unittest.main()
