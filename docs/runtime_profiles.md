# Runtime Profiles

`desktop_act` supports profile-driven execution semantics.

## Profiles

### `basic_reliable` (default)
- Deterministic behavior
- Observation before risky actions
- Safe mode on (destructive actions require `confirm=true`)
- Unknown actions disallowed
- Canary pre-checks enabled
- Conservative automatic fallback tuning enabled

### `balanced`
- General-purpose profile
- Observation before risky actions
- Unknown actions allowed when configured
- Canary pre-checks enabled
- Conservative automatic fallback tuning enabled

### `strict`
- Fail-fast posture
- Tight budgets
- Safe mode on
- Unknown actions disallowed
- Canary pre-checks enabled
- Automatic fallback tuning disabled

### `unrestricted`
- Maximum flexibility
- Broad budgets
- Observation optional
- Unknown actions allowed
- Canary pre-checks disabled
- Automatic fallback tuning disabled

## Overrides

Use `runtime_options` to override:

- `max_steps`
- `max_runtime_ms`
- `default_retries`
- `observe_before_risky`
- `safe_mode`
- `allow_unknown_actions`
- `canary_checks`
- `auto_fallback`
- `fallback_policy` (`none|conservative|aggressive`)
