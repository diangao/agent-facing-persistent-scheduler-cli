# agent-facing persistent scheduler CLI

A durable time signal for agents. Local-first. The core emits due events; an
adapter decides what to do.

It is not an agent framework and not a cron replacement with a different
syntax. It gives agents durable schedule objects, lifecycle commands, run
logs, and inert due events. The payload is information for the runtime;
action delivery is an adapter concern.

## Why

Agents need time as an inspectable object:

- `create`, `list`, `show`, `update`, `cancel`, `snooze`, `log`
- durable local state instead of hidden process memory
- auditable history of when each rule emitted or missed an event
- due/missed events that can be routed into any runtime adapter

## Install for development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

## Quick start

Create a one-shot rule:

```bash
agent-scheduler create \
  --title "check the draft" \
  --in 10m \
  --payload '{"type":"agent.reminder","text":"Check the draft."}'
```

Payloads can also come from a file or stdin:

```bash
agent-scheduler create --title "daily check" --in 10m --payload-file payload.json
cat payload.json | agent-scheduler create --title "daily check" --in 10m --payload-stdin
```

List rules:

```bash
agent-scheduler list
```

Update a rule without changing its id or run history:

```bash
agent-scheduler update r_abc123 --in 1h --payload '{"type":"agent.reminder","text":"Check the draft later."}'
```

Emit due events as NDJSON:

```bash
agent-scheduler run-due --output ndjson
```

Run a polling daemon:

```bash
agent-scheduler daemon --poll-interval 30 --output ndjson
```

Snooze the next fire:

```bash
agent-scheduler snooze r_abc123 --for 10m
```

## Event contract

`run-due` and `daemon` emit one JSON object per due rule. A freshly due rule
emits `scheduler.fire`:

```json
{
  "type": "scheduler.fire",
  "rule_id": "r_...",
  "run_id": "run_...",
  "title": "check the draft",
  "scheduled_for": "2026-05-29T20:42:00Z",
  "fired_at": "2026-05-29T20:42:01Z",
  "payload": {
    "type": "agent.reminder",
    "text": "Check the draft."
  }
}
```

If a rule is past due by more than the missed-fire grace window, the core emits
`scheduler.missed` instead of silently late-firing:

```json
{
  "type": "scheduler.missed",
  "rule_id": "r_...",
  "title": "check the draft",
  "scheduled_for": "2026-05-29T16:00:00Z",
  "detected_at": "2026-05-29T20:42:01Z",
  "missed_by_seconds": 16921,
  "payload": {
    "type": "agent.reminder",
    "text": "Check the draft."
  }
}
```

Missed one-shot rules are disabled after the missed event. Missed interval
rules emit one missed event and advance to the next future slot. The core never
burst-fires skipped slots after downtime.

Adapters are intentionally outside the core CLI. A host bridge can read this
event and decide how to deliver it.

## Advanced routing metadata

For multiple adapters sharing one local scheduler store, rules can carry
optional opaque routing labels:

```bash
agent-scheduler create \
  --title "check the draft" \
  --namespace "claude-code" \
  --target "session:main" \
  --in 10m \
  --payload '{"type":"agent.reminder","text":"Check the draft."}'
```

`namespace` and `target` are opaque routing labels. The core stores and emits
them, but never interprets them. Adapters can use them to decide whether an
event belongs to a specific runtime, agent, or session. They are not auth,
ownership, or data-isolation fields; use a separate `--db` for a separate trust
boundary.

## Current scope

Implemented:

- local SQLite store under `~/.agent-scheduler/scheduler.sqlite3`
- one-shot rules via `--at` or `--in`
- interval rules via `--every`
- `create`, `list`, `show`, `update`, `cancel`, `snooze`, `log`, `run-due`, `fire-now`, `daemon`
- payload from `--payload`, `--payload-file`, or `--payload-stdin`
- optional opaque `--namespace` and `--target` routing metadata
- NDJSON fire and missed events

Not yet implemented:

- cron expression parser
- named adapters
- remote sync
- adapter policy gates and delivery-specific rate limits

Policy belongs in the host application. The scheduler core only creates
durable time objects and emits inert events. The payload is information for
the runtime; actions belong to the adapter.
