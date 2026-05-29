from __future__ import annotations

from datetime import UTC, datetime
import io
import json
from pathlib import Path
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

            code, out = self.run_cli(db, "run-due", "--now", "2026-05-29T20:00:01Z")
            self.assertEqual(code, 0)
            events = [json.loads(line) for line in out.splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "scheduler.fire")
            self.assertEqual(events[0]["payload"], {"text": "check draft"})

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


if __name__ == "__main__":
    unittest.main()
