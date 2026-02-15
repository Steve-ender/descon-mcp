# Session Model

NovaForge state is session-scoped. Each session has independent:

- lifecycle (`active`, `paused`, `cancel_requested`, `emergency_stop`)
- focus/binding hints (`focused_window_*`, `bound_window_*`)
- counters (`action_count`)
- recording state (`recording_*`)
- transaction state (`transaction_*`)

## Session Identity

- Most tools accept optional `session_id`.
- If omitted, the runtime resolves to the current default session.
- `desktop_session_start(session_id=...)` sets/uses that session and makes it the default for subsequent calls that omit `session_id`.

## Auto-Start Behavior

- `assert_can_run(...)` enforces policy for the resolved session.
- When `NOVAFORGE_AUTO_START_SESSION=true`, the resolved session is started automatically on first action.

## Multi-Session Semantics

- Sessions are isolated in memory; mutating one session does not mutate another.
- Activity glow is process-level and remains shared.
- Engine side effects (mouse/keyboard/window actions) are host-level by nature; session isolation governs orchestration state, not OS hardware partitioning.

## Tooling Guidance

- For deterministic multi-client automation, pass explicit `session_id` on each call.
- For single-client usage, omitting `session_id` is valid and uses default-session behavior.
- Health/session tools expose current session metadata and known session counts.

## Script Conventions

Operational scripts use explicit stable session IDs:

- `scripts/production_smoke.py`: `script_production_smoke`
- `scripts/reliability_eval.py`: `script_reliability_eval`
- `scripts/basic_reliable_benchmark.py`: `script_basic_reliable_benchmark`
- `scripts/basic_reliable_benchmark_live.py`: `script_basic_reliable_benchmark_live`

This prevents script runs from polluting each other's state when executed in the same process.
