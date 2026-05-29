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

