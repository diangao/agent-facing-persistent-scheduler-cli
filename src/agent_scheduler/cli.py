from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time

from .store import DEFAULT_DB, Rule, SchedulerStore
from .timeparse import format_dt, parse_datetime, parse_due_time, utc_now


def _json_arg(value: str) -> dict:
    return _parse_json_payload(value)


def _parse_json_payload(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("payload must be a JSON object")
    return parsed


def _load_payload(args: argparse.Namespace) -> dict:
    if args.payload is not None:
        return args.payload
    if args.payload_file is not None:
        return _parse_json_payload(Path(args.payload_file).read_text(encoding="utf-8"))
    if args.payload_stdin:
        return _parse_json_payload(sys.stdin.read())
    raise ValueError("provide payload with --payload, --payload-file, or --payload-stdin")


def _rule_to_dict(rule: Rule) -> dict:
    return {
        "id": rule.id,
        "title": rule.title,
        "namespace": rule.namespace,
        "target": rule.target,
        "schedule_kind": rule.schedule_kind,
        "next_fire_at": format_dt(rule.next_fire_at),
        "enabled": rule.enabled,
        "interval_seconds": rule.interval_seconds,
        "payload": rule.payload,
        "created_at": format_dt(rule.created_at),
        "updated_at": format_dt(rule.updated_at),
    }


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _store(args: argparse.Namespace) -> SchedulerStore:
    return SchedulerStore(Path(args.db))


def cmd_create(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        next_fire_at = parse_due_time(at=args.at, in_=args.in_)
        rule = store.create_rule(
            title=args.title,
            next_fire_at=next_fire_at,
            payload=_load_payload(args),
            interval=args.every,
            namespace=args.namespace,
            target=args.target,
        )
        _print_json(_rule_to_dict(rule))
        return 0
    finally:
        store.close()


def cmd_list(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        _print_json([_rule_to_dict(rule) for rule in store.list_rules(include_disabled=args.all)])
        return 0
    finally:
        store.close()


def cmd_show(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        _print_json(_rule_to_dict(store.get_rule(args.rule_id)))
        return 0
    finally:
        store.close()


def cmd_cancel(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        _print_json(_rule_to_dict(store.cancel_rule(args.rule_id)))
        return 0
    finally:
        store.close()


def cmd_update(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        next_fire_at = None
        if args.at or args.in_:
            next_fire_at = parse_due_time(at=args.at, in_=args.in_)
        payload = None
        if args.payload is not None or args.payload_file is not None or args.payload_stdin:
            payload = _load_payload(args)
        rule = store.update_rule(
            args.rule_id,
            title=args.title,
            next_fire_at=next_fire_at,
            payload=payload,
            interval=args.every,
            namespace=args.namespace,
            target=args.target,
        )
        _print_json(_rule_to_dict(rule))
        return 0
    finally:
        store.close()


def cmd_snooze(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        next_fire_at = parse_due_time(at=args.until, in_=args.for_, now=utc_now())
        _print_json(_rule_to_dict(store.snooze_rule(args.rule_id, next_fire_at=next_fire_at)))
        return 0
    finally:
        store.close()


def _run_due_once(store: SchedulerStore, *, now: datetime | None, limit: int | None) -> int:
    emitted = 0
    for rule in store.due_rules(now=now, limit=limit):
        _print_json(store.fire_rule(rule, fired_at=now))
        emitted += 1
    return emitted


def cmd_run_due(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        now = parse_datetime(args.now) if args.now else None
        _run_due_once(store, now=now, limit=args.limit)
        return 0
    finally:
        store.close()


def cmd_fire_now(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        rule = store.get_rule(args.rule_id)
        _print_json(store.fire_rule(rule))
        return 0
    finally:
        store.close()


def cmd_daemon(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        while True:
            _run_due_once(store, now=None, limit=args.limit)
            sys.stdout.flush()
            time.sleep(args.poll_interval)
    finally:
        store.close()


def cmd_log(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        _print_json(store.log(args.rule_id, limit=args.limit))
        return 0
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-scheduler",
        description="A local-first persistent scheduler CLI for agent runtimes.",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")

    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="create a schedule rule")
    create.add_argument("--title", required=True)
    create.add_argument("--namespace", help="advanced: opaque adapter/runtime namespace")
    create.add_argument("--target", help="advanced: opaque adapter-specific route key")
    due = create.add_mutually_exclusive_group(required=True)
    due.add_argument("--at", help="ISO datetime, e.g. 2026-05-29T20:00:00Z")
    due.add_argument("--in", dest="in_", help="relative duration, e.g. 10m, 2h, 1d")
    create.add_argument("--every", help="repeat interval, e.g. 30m, 1h, 1d")
    payload = create.add_mutually_exclusive_group(required=True)
    payload.add_argument("--payload", type=_json_arg, help="JSON object payload")
    payload.add_argument("--payload-file", help="read JSON object payload from file")
    payload.add_argument("--payload-stdin", action="store_true", help="read JSON object payload from stdin")
    create.set_defaults(func=cmd_create)

    list_cmd = sub.add_parser("list", help="list rules")
    list_cmd.add_argument("--all", action="store_true", help="include disabled rules")
    list_cmd.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="show one rule")
    show.add_argument("rule_id")
    show.set_defaults(func=cmd_show)

    cancel = sub.add_parser("cancel", help="disable one rule")
    cancel.add_argument("rule_id")
    cancel.set_defaults(func=cmd_cancel)

    update = sub.add_parser("update", help="update one rule without changing its id")
    update.add_argument("rule_id")
    update.add_argument("--title")
    update.add_argument("--namespace", help="advanced: opaque adapter/runtime namespace; pass empty string to clear")
    update.add_argument("--target", help="advanced: opaque adapter-specific route key; pass empty string to clear")
    due_update = update.add_mutually_exclusive_group()
    due_update.add_argument("--at", help="ISO datetime, e.g. 2026-05-29T20:00:00Z")
    due_update.add_argument("--in", dest="in_", help="relative duration, e.g. 10m, 2h, 1d")
    update.add_argument("--every", help="repeat interval, e.g. 30m, 1h, 1d")
    payload_update = update.add_mutually_exclusive_group()
    payload_update.add_argument("--payload", type=_json_arg, help="JSON object payload")
    payload_update.add_argument("--payload-file", help="read JSON object payload from file")
    payload_update.add_argument("--payload-stdin", action="store_true", help="read JSON object payload from stdin")
    update.set_defaults(func=cmd_update)

    snooze = sub.add_parser("snooze", help="push the next fire time later")
    snooze.add_argument("rule_id")
    snooze_due = snooze.add_mutually_exclusive_group(required=True)
    snooze_due.add_argument("--until", help="ISO datetime, e.g. 2026-05-29T20:00:00Z")
    snooze_due.add_argument("--for", dest="for_", help="relative duration, e.g. 10m, 2h, 1d")
    snooze.set_defaults(func=cmd_snooze)

    run_due = sub.add_parser("run-due", help="emit due scheduler.fire events as NDJSON")
    run_due.add_argument("--now", help="override current time, ISO datetime")
    run_due.add_argument("--limit", type=int, help="maximum due rules to fire")
    run_due.add_argument("--output", choices=["ndjson"], default="ndjson")
    run_due.set_defaults(func=cmd_run_due)

    fire_now = sub.add_parser("fire-now", help="emit one scheduler.fire event immediately")
    fire_now.add_argument("rule_id")
    fire_now.set_defaults(func=cmd_fire_now)

    daemon = sub.add_parser("daemon", help="poll forever and emit due events")
    daemon.add_argument("--poll-interval", type=float, default=30.0)
    daemon.add_argument("--limit", type=int, help="maximum due rules to fire per poll")
    daemon.add_argument("--output", choices=["ndjson"], default="ndjson")
    daemon.set_defaults(func=cmd_daemon)

    log = sub.add_parser("log", help="show run log")
    log.add_argument("rule_id", nargs="?")
    log.add_argument("--limit", type=int, default=50)
    log.set_defaults(func=cmd_log)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (KeyError, ValueError) as exc:
        print(f"agent-scheduler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
