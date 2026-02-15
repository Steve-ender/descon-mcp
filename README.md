# Descon MCP (Prototype)

Local-first MCP server for full Windows desktop automation.

## Capabilities
- Window and process control (launch/focus/list/close)
  - Deterministic targeting by `window_handle` / `window_pid` / `title_regex`
  - Session window binding tools: `desktop_bind_window`, `desktop_unbind_window`, `desktop_window_binding_status`
  - Foreground tools: `desktop_get_foreground_window`, `desktop_focus_guard`
- Mouse + keyboard automation (including `mouse_down`, `mouse_up`, `drag_to`)
- Fast screen capture
- Template matching + click targeting (OpenCV)
- High-level multi-step orchestration via `desktop_act`
  - Failure hooks: auto rollback hint + clean transaction abort on step failure
  - Typed step validation and structured error codes for planner-friendly recovery
- UIA-first element actions in `desktop_act` with fallback:
  - `click_element` / `element_click` -> UIA -> template -> OCR text
  - `type_element` / `element_type` -> UIA typing -> fallback click + type
  - Bound-window aware targeting when step omits explicit window fields
  - Foreground guard policy per mutating step (`require_foreground_match`, mismatch mode, retries)
- Dedicated action primitives:
  - `desktop_element_query`
  - `desktop_element_action` (invoke/click/type/select/toggle/expand/collapse/set_value/scroll)
  - `desktop_wait_for`
  - `desktop_assert`
- Intent compiler:
  - `desktop_plan_compile` (intent list -> deterministic `desktop_act` plan, with profile policy)
  - Supports transaction wrapping and automatic checkpoint insertion
- Transaction and recovery tools:
  - `desktop_transaction_start|status|checkpoint|rollback_hint|end|export`
- Observation and diagnostics:
  - `desktop_observe` (windows + cursor + transaction + optional screenshot/OCR preview)
  - `desktop_diagnose_permissions` (host/target privilege diagnostics + UIPI hints)
- Recorder and replay:
  - `desktop_recording_start|stop|status|get|clear|save|load|replay`
- Deterministic plan tooling:
  - `desktop_recording_plan`
  - `desktop_plan_normalize`
  - `desktop_plan_diff`
  - `desktop_plan_load`
  - `desktop_plan_save`
- Hybrid OCR text extraction:
  - `local_model` (RapidOCR ONNX local runtime)
  - `tesseract` (fallback)
  - `auto` (local_model -> tesseract)
  - `auto_with_host` / `host_model` (explicit opt-in only)
- Safety controls (allowlist, kill switch, run limits)

## Run
```powershell
cd C:\Users\obeng\Project_Descon_MCP\prototype\descon_mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[ocr,vision]
descon-mcp
```

## Quality Gates
```powershell
cd C:\Users\obeng\Project_Descon_MCP\prototype\descon_mcp
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pip install -r requirements-release.txt
.\.venv\Scripts\python.exe scripts\release_gate.py
```
The gate runs `compileall`, `pytest`, `scripts/production_smoke.py`, `scripts/basic_reliable_benchmark.py`, and `scripts/package_release.py`, then writes an audit report under `artifacts/release_gate/`.

## Reliability Eval Harness
```powershell
cd C:\Users\obeng\Project_Descon_MCP\prototype\descon_mcp
.\.venv\Scripts\python.exe scripts\reliability_eval.py --preset core --repeats 20
```
This writes scenario-level reliability metrics (pass rate, p95 latency, flakiness, MTTR attempts, error-code histogram) to `artifacts/reliability_eval/`.

Live desktop preset:
```powershell
.\.venv\Scripts\python.exe scripts\reliability_eval.py --preset live_desktop --repeats 5
```

Optional baseline comparison:
```powershell
.\.venv\Scripts\python.exe scripts\reliability_eval.py --baseline .\artifacts\reliability_eval\reliability_eval_latest.json
```

Optional strict release gate integration:
```powershell
$env:DESCON_STRICT_RELIABILITY_GATE = '1'
$env:DESCON_RELIABILITY_PRESET = 'core'   # or live_desktop
$env:DESCON_RELIABILITY_REPEATS = '10'
$env:DESCON_STRICT_SOAK_GATE = '1'
$env:DESCON_SOAK_DURATION_SECONDS = '120'
$env:DESCON_SOAK_WORKERS = '3'
$env:DESCON_SOAK_MIN_PASS_RATE = '0.99'
$env:DESCON_SOAK_MIN_RUNS_PER_WORKER = '1'
.\.venv\Scripts\python.exe scripts\release_gate.py
```

Standalone soak runner:
```powershell
.\.venv\Scripts\python.exe scripts\soak_sessions.py --duration-seconds 120 --workers 3 --preset core --min-pass-rate 0.99 --min-runs-per-worker 1
```
This writes multi-session soak reports to `artifacts/soak/`.

## Safety Env Vars
- `DESCON_ALLOWLIST` comma-separated executable names allowed for launch/close
- `DESCON_REQUIRE_ALLOWLIST` enforce allowlist for process actions (default `false`)
- `DESCON_ALLOWLIST_HARD_ENFORCE` hard-block disallowed process launch/close when allowlist is enabled (default `false`)
- `DESCON_MAX_ACTIONS` max actions per session (`0` disables limit; default `0`)
- `DESCON_AUTO_START_SESSION` auto-start session on first tool call (default `true`)
- `desktop_emergency_stop` is a hard stop until manually cleared via `desktop_emergency_stop(enabled=false)`
- `DESCON_INPUT_FAILSAFE` PyAutoGUI corner failsafe (default `false`)
- `DESCON_ENABLE_HOST_OCR` set `true` to allow host-model OCR paths
- `DESCON_WINDOW_RESOLVE_FALLBACK_FOREGROUND` fallback to current foreground when explicit window target is unresolved (default `true`)
- `DESCON_STRICT_WINDOW_VISIBILITY` fail when target remains invisible after revive attempts (default `false`)
- `DESCON_STRICT_PRIVILEGE_CHECK` fail on elevation mismatch (default `false`)
- `DESCON_ALLOW_UNKNOWN_ACTIONS` map unknown `desktop_act` actions to no-op warnings (default `true`)
- `DESCON_BEST_EFFORT_VISION` return non-fatal template miss results where possible (default `true`)
- `DESCON_BEST_EFFORT_OCR` return non-fatal empty OCR results where possible (default `true`)
- `DESCON_ARTIFACTS_DIR` absolute/relative directory for screenshots, recordings, plans, transactions, smoke reports
- `desktop_act` runtime loop controls:
  - `runtime_profile`: `basic_reliable|strict|balanced|unrestricted`
  - `runtime_options`: override budgets/guards (`max_steps`, `max_runtime_ms`, `default_retries`, `observe_before_risky`, `safe_mode`, `allow_unknown_actions`)

See `docs/execution_contract.md`, `docs/runtime_profiles.md`, and `docs/session_model.md` for contract, profile, and session semantics.
- `DESCON_ENABLE_ACTIVITY_GLOW` show edge glow while session is active (`true` by default)
- `DESCON_ACTIVITY_GLOW_COLOR` hex color for edge glow (default `#00ff88`)
- `DESCON_ACTIVITY_GLOW_THICKNESS` edge thickness in pixels (default `14`)

## Notes
- OCR requires Tesseract binary installed and available in PATH.
- Host-model OCR is disabled by default and requires both `DESCON_ENABLE_HOST_OCR=true` and MCP sampling support.
- This prototype is local-only and does not require cloud services.
- Process close by name uses exact executable-name matching (no substring termination).
- App launch defaults to structured non-shell execution (`shell_mode=false`).

## Quick Replay Flow
1. `desktop_session_start`
2. `desktop_recording_start`
3. Run normal actions (`desktop_click`, `desktop_type`, `desktop_act`, etc.)
4. `desktop_recording_stop`
5. `desktop_recording_replay`

## Compile Flow
1. Build high-level `intents` (e.g. `focus_window`, `click_element`, `type_element`, `wait_for`, `assert`)
2. Call `desktop_plan_compile` with profile `fast|balanced|stubborn`
   - Optional: `wrap_in_transaction=true`, `auto_checkpoints=major|all|none`
3. Execute returned `plan` using `desktop_act`
   - `desktop_act` supports `on_error_generate_rollback_hint`, `on_error_end_transaction`, `on_error_max_hints`



## Production Packaging
```powershell
cd C:\Users\obeng\Project_Descon_MCP\prototype\descon_mcp
.\.venv\Scripts\python.exe scripts\package_release.py
```
This produces wheel/sdist in `dist/` and checksum manifest files in `artifacts/release/`.

See `docs/deployment.md` for deployment runbook details.


## Security Gate
- `scripts/security_gate.py` runs `pip-audit` and writes `artifacts/security/security_gate_latest.json`.
- Set `DESCON_STRICT_SECURITY_GATE=1` to make security findings block `scripts/release_gate.py`.
- Set `DESCON_STRICT_SOAK_GATE=1` to require multi-session soak pass in `scripts/release_gate.py`.

Use `security-ignore.txt` to track explicit vulnerability exceptions (one ID per line) with documented rationale.
