# Execution Contract

Descon `desktop_act` step outputs must be canonical and machine-readable.

## Step Envelope

Each step in `data.steps` includes:

- `name: str`
- `action: str`
- `attempt: int`
- `ok: bool`
- `data: dict`
- `error: dict | null`
- `observation: { pre: dict | null, post: dict | null }`
- `confirmation: { status: str, reason: str, evidence: dict }`
- `runtime_meta: { profile: str, strategy_path: list[str] }`

## Runtime Metadata

Top-level `data.runtime` includes:

- `profile: str`
- `state_handoff: dict`
- `transcript: list[dict]`

## Error Contract

`error` always includes:

- `code: str`
- `message: str`
- `hint: str`
- `details: dict`

## Invariants (basic_reliable)

1. Unknown actions are blocked unless runtime/profile allows no-op.
2. Mutating actions include pre/post observations whenever possible.
3. Every successful step includes a `confirmation` object.
4. Budgets are enforced (`max_steps`, `max_runtime_ms`).
