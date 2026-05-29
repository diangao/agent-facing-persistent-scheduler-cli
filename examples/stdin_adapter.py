"""Reference adapter: consume scheduler NDJSON from stdin.

Usage:

    agent-scheduler daemon --output ndjson | python examples/stdin_adapter.py

This is a *reference* — it does not actually deliver anything. It prints
what it would deliver and points out exactly where a real consumer
should add its own policy (auth, rate limiting, runtime injection).

The scheduler core is intentionally inert. This adapter is the place
where action delivery, rate caps, and any runtime-specific wrapping
belong. See SECURITY.md and docs/design-notes.md for the full
architectural invariant.
"""

from __future__ import annotations

import json
import sys


def route_label(event: dict) -> str:
    namespace = event.get("namespace")
    target = event.get("target")
    if namespace and target:
        return f" namespace={namespace!r} target={target!r}"
    if namespace:
        return f" namespace={namespace!r}"
    if target:
        return f" target={target!r}"
    return ""


def handle_fire(event: dict) -> None:
    """A real consumer would deliver the fire to its agent runtime here.

    Recommended steps for a real implementation:

      1. Apply your own rate cap on *delivered actions* (not on fires).
      2. Wrap the payload in whatever envelope your runtime expects.
      3. Hand it to your runtime (subprocess stdin, message queue, API
         call — whatever your integration uses).
      4. Record the delivered/skipped outcome in your own audit log.

    The scheduler core has already recorded that this fire was emitted;
    delivery state belongs to you.
    """
    print(
        f"[fire] rule={event['rule_id']}{route_label(event)} "
        f"title={event.get('title')!r} "
        f"scheduled_for={event['scheduled_for']} payload={event.get('payload')!r}"
    )


def handle_missed(event: dict) -> None:
    """A real consumer decides what to do with overdue rules.

    Three reasonable policies (the scheduler core has no opinion):

      - synthesize a fire-as-of-now and deliver it;
      - skip the miss and only act on future fires;
      - ask a user before deciding (useful for one-shot reminders).
    """
    print(
        f"[missed] rule={event['rule_id']}{route_label(event)} "
        f"scheduled_for={event['scheduled_for']} "
        f"missed_by={event['missed_by_seconds']}s"
    )


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"[skip] non-json line: {line!r}", file=sys.stderr)
            continue
        event_type = event.get("type")
        if event_type == "scheduler.fire":
            handle_fire(event)
        elif event_type == "scheduler.missed":
            handle_missed(event)
        else:
            print(f"[skip] unknown event type: {event_type!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
