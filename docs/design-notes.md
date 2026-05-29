# Design notes

## Architectural invariant

> Scheduler core owns time + state + log.
> Adapter owns action delivery + auth + rate caps + runtime injection.

This single boundary is the design center of this project. The CLI in this
repository is the scheduler core. Anything that fires a real action against a
real system — calling a webhook, running a shell command, injecting a turn into
an agent runtime, sending a message — belongs to an adapter, not to the core.

Two consequences follow directly:

- The core never owns secrets, network destinations, runtime handles, or
  delivery state.
- Rule creation is not, by itself, an execution surface. It becomes one only
  when an adapter binds the rule's fire event to an action.

If a future change would move action delivery, auth, rate limiting, or runtime
injection into the core, it should be rejected by reference to this invariant.

## Prior art

Three families of existing systems informed this design. None of them is a
direct dependency.

- **Reminder objects in collaboration runtimes.** Persistent reminder records
  with full lifecycle commands, anchored wakeups, and visible fire / log
  semantics. These are the closest reference for what an agent-facing
  scheduler should look like from the user's side: an inspectable object, not a
  hidden timer.
- **Feature-gated cron-style scheduler tools inside agent IDEs.** Useful design
  ideas, not a public dependency. Notable concepts: feature-gated tool
  registration, a session-only vs. durable storage split, missed one-shot
  surfacing instead of silent auto-execution, kill switches, per-rule auto
  expiry, and a hard cap on concurrent rules.
- **Gateway-style scheduler patterns in agent gateways.** A useful mechanism
  reference for durable stores, JSONL run logs, bounded timer loops, missed-run
  catchup, transient retry with backoff, manual run, and explicit CRUD. Action
  delivery in these systems is broader (webhooks, isolated agent turns,
  in-session events). This project intentionally does not include built-in
  action delivery; fire output is inert NDJSON only, and action delivery is a
  consumer-side responsibility.

## Mechanism inspiration

These mechanisms come from prior art and inform the scope and shape of this
core. They are described generically so the core can be implemented without
binding to any particular host runtime.

- **Durable object store.** Rules live on disk in a local store. The current
  implementation is a SQLite database under `~/.agent-scheduler/`. Process
  restart and file watch are the basic recovery primitives. There is no
  in-memory only mode.
- **Bounded timer loop.** The daemon sleeps for at most a fixed bound (default
  poll interval), reloads the store, computes due rules, fires them, persists
  the next fire time for recurring rules, and re-arms. No assumption of process
  liveness between ticks. No reliance on host wakeups.
- **Missed-fire surfacing, not auto-fire.** If the daemon restarts and finds a
  one-shot rule whose scheduled time is already in the past, the correct action
  is to surface the miss to the consumer, not to fire silently as if the time
  had arrived. The proposed shape for v0.1.x is a `scheduler.missed` event
  alongside `scheduler.fire`; consumers decide whether to treat the miss as a
  late fire or to skip it. See the event spec below.
- **Run logs.** Every fire — and, when added, every miss — appends a run-log
  entry that the CLI can list. The run log is part of the inert state the core
  owns; it is not an event stream over the network.
- **Per-rule cap, optional auto-expire, kill switch.** A rule may carry a
  maximum fire count, after which it transitions to a terminal state. A global
  kill switch allows an operator to disable all firing without deleting rules.
  These are mechanism features; rate caps tied to a downstream business action
  (for example, "do not send more than N messages per hour") are an adapter
  concern.

## Event contract

The core emits one JSON object per event line on stdout. There is no other
delivery target in the core.

### `scheduler.fire`

Emitted when a rule fires.

```json
{
  "type": "scheduler.fire",
  "rule_id": "r_...",
  "run_id": "run_...",
  "title": "check the draft",
  "scheduled_for": "2026-05-29T20:42:00Z",
  "fired_at": "2026-05-29T20:42:01Z",
  "payload": { "type": "agent.reminder", "text": "Check the draft." }
}
```

### `scheduler.missed` (proposed, not yet implemented)

Emitted on daemon startup or after a long sleep when a one-shot rule's
`scheduled_for` is already in the past. The core does not auto-fire; the
consumer reads the miss and decides.

```json
{
  "type": "scheduler.missed",
  "rule_id": "r_...",
  "title": "check the draft",
  "scheduled_for": "2026-05-29T16:00:00Z",
  "detected_at": "2026-05-29T20:42:01Z",
  "missed_by_seconds": 17041,
  "payload": { "type": "agent.reminder", "text": "Check the draft." }
}
```

The consumer's options on a `scheduler.missed`:

- Treat as a late fire — synthesize a `scheduler.fire` consumer-side and act on
  it.
- Skip — record the miss but do nothing.
- Ask a human — surface the miss to a user and gate the late-fire decision.

The core has no opinion among these.

## Design principles

- **Substrate, not framework.** This project does not own the runtime that
  reacts to fire events. It does not own auth. It does not own delivery. It
  does not bind to a specific agent product, message bus, or chat system.
- **Inert by default.** The default output is JSON on stdout. There is no
  webhook posting, no shell exec, no in-session injection in the core. An
  adapter that adds any of those must document its trust model.
- **Local-first.** Storage is local. There is no required server. A consumer
  may sync the store elsewhere, but the core does not.
- **Lifecycle parity with the rule object.** `create`, `list`, `show`,
  `update`, `cancel`, `snooze`, `log` are all first-class. A rule is an
  object, not a side effect of a command schedule.
- **Generic naming.** The core code and docs do not name any specific user,
  agent, transport, channel, host runtime, organization, or hardware. Adapter
  repositories are free to bind to anything; the core is not.

## Consumer integration shape

For reference, a consumer typically consumes the core in one of two ways:

- **Streaming subscriber.** Spawn `agent-scheduler daemon --output ndjson` as a
  long-lived subprocess and read events line by line. Each fire or miss is
  handed to whatever delivery / runtime injection the consumer owns. Auth,
  rate limits, and policy gates live entirely on the consumer side.
- **Polling drain.** Periodically invoke `agent-scheduler run-due --output
  ndjson` and process whatever is emitted. Useful in environments where a
  long-lived subprocess is awkward.

In both modes, rule creation is also a consumer-side action. The CLI is a
local executable; whether it should be reachable from a particular environment,
identity, or input source is the consumer's policy.

## What is intentionally out of scope

- Built-in webhook delivery.
- Built-in shell command execution.
- Built-in agent-runtime injection.
- Built-in remote storage or multi-host sync.
- Auth, identity, role-based access control.
- Business-level rate limiting (for example, "downstream sends per hour").
- A "trusted source" model for rule creation.

Each of the above is a legitimate concern. None of them belongs in the core.
