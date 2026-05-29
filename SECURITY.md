# Security model

The core scheduler is intentionally inert.

It stores durable rules and emits `scheduler.fire` events as NDJSON. It does
not send network requests, run shell commands, call agent runtimes, or deliver
messages by itself.

## Why this matters

A scheduler that can directly execute commands or POST to URLs turns rule
creation into an execution or SSRF surface. This project keeps that authority
outside the core. Consumers should read fire events and apply their own policy
before taking action.

## Core guarantees

- Local durable rules are inspectable through CLI commands.
- Every fire creates a run-log entry.
- Default output is inert JSON on stdout.
- Delivery, auth, rate limits, and runtime injection are adapter concerns.

## Adapter guidance

If an adapter adds webhook, shell, or agent-runtime delivery, it should document:

- who can create or update rules;
- what delivery targets are allowed;
- how rate limits are enforced;
- where fire and delivery logs are stored;
- whether failed delivery retries are bounded.

Do not treat scheduler rule creation as harmless once an action-delivery adapter
is enabled.

## Threat model

A more systematic view of the surfaces, so adapter authors know exactly which
guarantees they inherit and which they must add.

### Assets

- The rule store: rules, their payloads, and the run log.
- The fire-event stream emitted on stdout.
- Schedule integrity: the intended rule fires at the intended time, once.

### Trust boundaries

- **Rule-creation authority = write access to the store.** The CLI is one
  writer; direct access to the SQLite file is another. The core does not
  authenticate who created a rule or record its origin.
- **`payload` and `title` are opaque, untrusted content.** The core stores them
  and echoes them verbatim into fire events. It never validates, sanitizes, or
  interprets them.
- **The fire event is the core/consumer boundary** — an inert handoff. The
  core's authority ends at emitting the event on stdout.

### Attacker classes and surface analysis

1. **Local filesystem access to the store.** Can read payloads and the run log
   (confidentiality), and can insert, modify, disable, or cancel rules directly,
   bypassing the CLI and any CLI-level checks. The mitigation is the OS, not the
   core: the store file's permissions *are* the rule-creation and
   confidentiality boundary. Keep it user-private (the default
   `~/.agent-scheduler/` location); do not point `--db` at a shared or
   world-writable path.

2. **Untrusted payload / title content.** A rule's `payload` and `title` flow
   verbatim to the consumer. This is only dangerous if a consumer treats
   fire-event content as instructions to execute rather than as data. Canonical
   consumer rule: **a fire is a wake signal, not authority to act.** Treat the
   payload as data; never exec, eval, or dispatch it. Trigger is not action.

3. **Rule flooding and catch-up bursts.** `run-due` and `daemon` process due
   rules; with no `--limit`, many due rules can still be emitted as one burst.
   For a single overdue rule, the core prevents silent downtime catch-up by
   emitting `scheduler.missed` instead of burst-firing skipped slots. The core
   still applies no delivery rate cap by design. Consumers must bound the
   *downstream action* — count delivered actions, not core events — so a burst
   cannot amplify into a flood.

4. **Caller-controlled time (`run-due --now`).** `--now` overrides the clock and
   can force not-yet-due rules to fire early. It is an operational and testing
   surface; do not expose `run-due --now` to untrusted callers.

### What the core does NOT do (explicitly out of scope)

- authenticate or authorize rule creators;
- validate, sanitize, or schema-check payloads;
- rate-limit or cap fires or deliveries;
- verify that a delivery actually happened;
- protect the store file (an OS responsibility);
- provide cross-agent isolation via routing metadata.

All of these belong to the consuming adapter or host.

### Routing metadata is not isolation

A rule may carry optional opaque labels such as `namespace` and `target` (or
`route_key`) so that, on a shared store with multiple subscribers, each
adapter can self-select which fire events it acts on. The core neither
parses these labels nor enforces them. They are routing hints, not access
controls.

Specifically:

- **They isolate DELIVERY, not DATA.** When an adapter rejects fire events
  that do not match its declared labels (adapter-conformance #3), one
  subscriber will not act on another subscriber's fires.
- **They do NOT isolate read or write access to the store.** Anything that
  can read the SQLite file can read every rule, payload, and run-log entry
  regardless of label. Anything that can write the SQLite file can create a
  rule under any label (the field is opaque, not authenticated).
- **The real cross-agent boundary is the store.** Mutually-trusting agents
  (same user, same trust domain) can share a store and use `namespace` /
  `target` for routing ergonomics. Agents that must not read or forge each
  other's rules need separate stores, configured via `--db`.

Naming reflects this: the labels are called `namespace` / `target` /
`route_key`, not `tenant` or `owner`. Those terms would imply isolation the
core does not provide.

### Hardening notes for the core itself

- **Run-log truthfulness.** The run log records each fire with status `emitted`
  rather than `delivered` — the core can observe what it emitted on stdout but
  cannot observe whether a consumer actually delivered the event. The audit log
  reflects only what the core can verify.
- **Make catch-up explicit.** Overdue rules surface as distinct
  `scheduler.missed` events rather than silent catch-up fires, so consumers can
  choose fire-as-now versus skip. This preserves the inert-core contract and
  makes catch-up auditable.
