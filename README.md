# agent-facing persistent scheduler CLI

A small local-first scheduler substrate for agents.

It is not an agent framework and not a cron replacement with a different syntax.
It gives agents durable schedule objects, lifecycle commands, run logs, and
runtime-friendly fire events that another bridge can deliver to an agent
runtime.

## Why

Agents need time as an inspectable object:

- `create`, `list`, `show`, `update`, `cancel`, `snooze`, `log`
- durable local state instead of hidden process memory
- auditable fire history
- due events that can be routed into any runtime adapter

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

`run-due` and `daemon` emit one JSON object per fired rule:

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

Adapters are intentionally outside the core CLI. A host bridge can read this
event and decide how to deliver it.

## Current scope

Implemented:

- local SQLite store under `~/.agent-scheduler/scheduler.sqlite3`
- one-shot rules via `--at` or `--in`
- interval rules via `--every`
- `create`, `list`, `show`, `cancel`, `log`, `run-due`, `fire-now`, `daemon`
- payload from `--payload`, `--payload-file`, or `--payload-stdin`
- NDJSON fire events
- `snooze` lifecycle command for pushing only the next fire

Not yet implemented:

- cron expression parser
- named adapters
- remote sync
- adapter policy gates and delivery-specific rate limits

Policy belongs in the host application. The scheduler core only creates durable
time objects and emits inert fire events.
