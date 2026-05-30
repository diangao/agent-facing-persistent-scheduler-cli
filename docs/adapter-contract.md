# Adapter contract

This document is for authors of adapters that bind this scheduler core to a
specific agent runtime (e.g. a Claude Code session, a Codex automation, an
in-house agent loop). It is the spec an official adapter MUST satisfy, and a
checklist a third-party adapter SHOULD use to stay in the substrate
discipline.

## Where adapters sit

The core CLI in this repository emits inert `scheduler.fire` and
`scheduler.missed` events on stdout. It does not deliver them anywhere, run
any code as a consequence of them, or interpret their payloads. Everything
between "a due event was emitted" and "an agent took an action" is the
adapter's responsibility.

This document does not duplicate `docs/design-notes.md` (which explains the
architectural invariant) or `SECURITY.md` (which lays out the threat model).
Read both before authoring an adapter.

## The four MUSTs

Every adapter — official or third-party — MUST satisfy these four
contracts. The conformance tests under `tests/adapter-conformance/`
mechanically verify the testable subset.

### 1. Trust LEVEL declaration

Every adapter MUST declare its trust level in its own README, immediately
under the project title, with the following three lines:

```
Target runtime: <name of target runtime>
Tool permissions: <read-only | bounded | bypassPermissions+full-tools>
Risk level: <mild | moderate | approaches-RCE>
```

The risk level encodes what rule creation actually means in this
deployment. An adapter that injects into a read-only target is mild. An
adapter that injects into a session with bypass/full-tools approaches RCE
on the host because every created rule becomes scheduled arbitrary
execution.

Trust level is per-adapter, not per-core. Different adapters targeting
different runtimes will declare different levels, and that is correct.

### 2. Malfunction FLOOR

Every adapter MUST hold these three invariants regardless of declared
trust level. They protect against bugs and runaway loops, not just
adversarial inputs; a trusted environment is still subject to programmer
error.

#### 2.1 Payload never executes locally

A fire event's `payload.text` (or any other payload field) is data the
adapter forwards to its runtime. It is never `os.system`'d, `eval`'d,
shell-expanded, or otherwise executed on the host where the adapter runs.

Conformance test: given a rule whose payload is
`{"text": "touch /tmp/pwned"}`, the adapter MUST treat the payload as
input to the runtime, not as a host command. After the test runs,
`/tmp/pwned` MUST NOT exist.

#### 2.2 Burst rate cap is on by default

When many rules become due at once — through downtime catch-up, a dense
recurrence, or a flood of new rules — the adapter MUST stop delivering at
a default cap and log the drops. The cap may be opt-out, not opt-in.

Conformance test: given 100 fire events emitted in a burst, the adapter
MUST stop at the default cap (e.g. 10/sec or 10/hour, picked by the
adapter), MUST log each drop in its own audit, and the runtime MUST NOT
receive more than the cap.

#### 2.3 Unknown route reject

If the adapter declares which `namespace` / `target` values it serves, it
MUST reject fire events that do not match those values, without injecting
them into the runtime, and MUST log the rejection.

Conformance test: given a fire event whose `target` does not match the
adapter's declared route, the adapter MUST NOT inject it, and MUST log a
route-reject audit entry. The runtime receives nothing.

### 3. Content boundary

> Scheduler command output / event metadata are operational evidence for
> the runtime/adapter; user-facing replies confirm or report failure
> naturally, without rule IDs, route labels, DB paths, or command
> transcripts.

The adapter's append-system-prompt MUST instruct the runtime to treat
scheduler `rule_id`, `namespace`, `target`, DB paths, raw CLI stdout, and
event-envelope fields as internal operational evidence. The user sees a
natural-language confirmation or failure report, not the internals.

This is what keeps a user from being told "rule r_abc123 created in
namespace foo at /var/...sqlite3" when they asked for a reminder.

### 4. Explicit reminder payload + fire-prompt minimalism

> Append-system-prompt teaches capability and response policy once; the
> fire event carries only the due reminder fact.

Adapters that expose reminders MUST store user-facing reminders as
structured payloads instead of requiring the runtime to preserve the
recipient inside free text. The recommended shape is:

```json
{
  "kind": "reminder",
  "action": "remind",
  "recipient": "<user | runtime>",
  "message": "<short reminder content>"
}
```

`recipient` is the subject/destination the adapter renders into the fired
turn. `message` is the compact reminder content; it should not need to
repeat the recipient. Optional debug fields such as `source_text` MAY be
stored for audit, but MUST NOT be included in the fired turn by default.

Concretely, when the adapter wraps a `scheduler.fire` event as input to its
runtime, the wrapped turn MUST be task-facing only:

- a structured metadata header containing `source`, `time`, optional
  `rule_id`, `target`, and `title`;
- a concise rendered reminder line derived from the payload's structured
  `recipient` and `message`.

The wrapped turn MUST NOT contain:

- role reflection ("reply naturally as <persona>");
- meta-instructions about the scheduler ("treat this as your own
  reminder", "explain it gently");
- anti-instructions ("do not explain the scheduler");
- pseudo-XML or other wrapping around the payload.

All response-shape guidance belongs in the adapter's append-system-prompt
capability card, fired once at session start, not in every fired turn.

The reference shape:

```text
[source=scheduler time=<ISO time>]
Reminder for <recipient>: <message>
```

That is enough. Anything more is friction; multiple-hop reasoning,
role-reflection passes, and meta-instruction parsing all cost latency and
correctness without buying alignment.

Recurrence metadata is a scheduler concern, not a fired-turn concern. A
recurring rule's fire event MUST be identical in shape to a one-shot
rule's fire event: the runtime sees a due reminder, not "this is the Nth
occurrence" or "next iteration at X". `every`, `next_fire_at`, and run
counts live in the scheduler and the audit log; the runtime can fetch
them via `agent-scheduler show <rule_id>` or `log <rule_id>` if it
explicitly needs them.

## Official adapter system-prompt-append template

This template is what an official adapter SHOULD append to its target
runtime's system prompt. It teaches capability and response policy once,
and is the natural place to add anything the runtime needs to know about
the scheduler.

Adapters render this template in one of two forms, chosen by the
adapter's deployment topology.

### Single-runtime form (A-first default)

When the adapter targets a single runtime and fixes the route, the
appended capability card SHOULD hide `--namespace` and `--target` from
the runtime. The runtime never needs to know about routing; that is the
adapter's concern.

```md
## Scheduler / Future Follow-Up Tool

You have access to a local persistent scheduler through `agent-scheduler`.
Use it when the user asks for a future reminder/follow-up, or when a task
depends on future time/state and should not be handled by sleeping,
waiting in-process, or relying on memory alone.

Available commands:

- `agent-scheduler create --at <ISO time> --payload '<JSON object>'`
- `agent-scheduler create --in <duration like 10m / 2h / 1d> --payload '<JSON object>'`
- `agent-scheduler create --every <duration like 1h / 1d / 1w> --payload '<JSON object>'` (recurring)
- `agent-scheduler create --random-daytime --window HH:MM-HH:MM --timezone <IANA tz> --count <N> --payload '<JSON object>'` (fixed N random fires per day inside the wall-clock window)
- `agent-scheduler create --random-daytime --window HH:MM-HH:MM --timezone <IANA tz> --count-range MIN-MAX --payload '<JSON object>'` (variable fires per day; core samples N within [MIN, MAX] and stores that day's sorted fire-time plan)
- `agent-scheduler list / show <rule_id> / update <rule_id> / snooze <rule_id> / cancel <rule_id> / log <rule_id>`

When creating a user-facing reminder, use an explicit payload such as:

`{"kind":"reminder","action":"remind","recipient":"user","message":"drink water"}`

When creating a runtime self-check, use `recipient:"runtime"`. Keep
`message` short; the adapter renders the recipient later. Do not put
scheduler internals in the payload. Do not claim a reminder is scheduled
unless the command succeeds.

Scheduler command output, rule IDs, namespace/target labels, DB paths, and
command transcripts are internal operational evidence. User-facing replies
should confirm or report failure naturally, without exposing those
details.

When a scheduled rule fires, it will return as a scheduler event in this
same runtime. Treat it as your own reminder/follow-up reaching its time.
The fired turn carries only the due reminder fact; response style and
content-boundary rules come from this capability card, not from the
fired turn.
```

### Multi-runtime form (advanced)

When a single scheduler daemon serves multiple runtimes and the adapter
needs the runtime to participate in routing (e.g. an in-house multi-agent
host), the appended capability card MAY expose `--namespace` and
`--target`. It MUST also explain that they are routing labels, not
authentication or data isolation.

```md
## Scheduler / Future Follow-Up Tool

You have access to a local persistent scheduler through `agent-scheduler`.
Use it when the user asks for a future reminder/follow-up, or when a task
depends on future time/state and should not be handled by sleeping,
waiting in-process, or relying on memory alone.

Always use namespace `<adapter-namespace>` and target `<runtime-target>`
so the outer daemon routes the fire back to this runtime:

- `agent-scheduler create --namespace <adapter-namespace> --target <runtime-target> --at <ISO time> --payload '<JSON object>'`
- `agent-scheduler create --namespace <adapter-namespace> --target <runtime-target> --in <duration> --payload '<JSON object>'`
- `agent-scheduler create --namespace <adapter-namespace> --target <runtime-target> --every <duration> --payload '<JSON object>'` (recurring)
- `agent-scheduler create --namespace <adapter-namespace> --target <runtime-target> --random-daytime --window HH:MM-HH:MM --timezone <IANA tz> --count <N> --payload '<JSON object>'` (fixed N random fires per day inside the wall-clock window)
- `agent-scheduler create --namespace <adapter-namespace> --target <runtime-target> --random-daytime --window HH:MM-HH:MM --timezone <IANA tz> --count-range MIN-MAX --payload '<JSON object>'` (variable fires per day; core samples N within [MIN, MAX] and stores that day's sorted fire-time plan)
- `agent-scheduler list / show / update / snooze / cancel / log`

Namespace and target are opaque routing labels. They do not authenticate
you, do not isolate your rules from other rules on the same store, and do
not protect any payload contents. Real cross-runtime isolation is one
store per trust boundary, configured by the host.

When creating a user-facing reminder, use an explicit payload such as:

`{"kind":"reminder","action":"remind","recipient":"user","message":"drink water"}`

When creating a runtime self-check, use `recipient:"runtime"`. Keep
`message` short; the adapter renders the recipient later. Do not put
scheduler internals in the payload. Do not claim a reminder is scheduled
unless the command succeeds.

Scheduler command output, rule IDs, namespace/target labels, DB paths, and
command transcripts are internal operational evidence. User-facing replies
should confirm or report failure naturally, without exposing those
details.

When a scheduled rule fires, it will return as a scheduler event in this
same runtime. Treat it as your own reminder/follow-up reaching its time.
The fired turn carries only the due reminder fact; response style and
content-boundary rules come from this capability card, not from the
fired turn.
```

## Reference consumer

`examples/stdin_adapter.py` is a generic inert consumer. It demonstrates
the subscribe / route / wrap shape without performing real delivery, and
is a good first integration test for a new adapter project: confirm that
events flow, then layer real runtime injection on top.

## Conformance suite

`tests/adapter-conformance/` (not yet shipped) will provide a runnable
test harness that any adapter project can point at its own binary to
verify the three malfunction-floor invariants and the content-boundary
rule. Until that suite ships, each adapter SHOULD write the four checks
into its own test suite.
