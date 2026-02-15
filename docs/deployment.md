# Deployment Guide

## Scope
This guide covers production deployment of `descon-mcp` on Windows hosts for Codex CLI usage.

## 1. Prepare Host
- Install Python 3.11+ (64-bit).
- Install Tesseract if OCR fallback is needed.
- Ensure target desktop session is available (interactive user session required for UI automation).

## 2. Install From Wheel (Recommended)
```powershell
cd C:\deploy\descon_mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --no-deps .\dist\descon_mcp-0.1.0-py3-none-any.whl
python -m pip install -r .\requirements-production.txt
python -m pip install -r .\requirements-release.txt
```

## 3. Verify Release Integrity
```powershell
Get-Content .\artifacts\release\package_manifest_latest.json
```
Check artifact `sha256` values against your release channel records before install.

## 4. Validate Runtime
```powershell
.\.venv\Scripts\python.exe scripts\release_gate.py
```
Expected result: `OK` and a report under `artifacts\release_gate\`.

## 5. Start Server
```powershell
.\.venv\Scripts\descon-mcp
```

## 6. Operational Recommendations
- Run one MCP server instance per interactive desktop session.
- Keep `desktop_emergency_stop` enabled during incident response until explicitly cleared.
- Use health endpoints (`desktop_healthcheck`, `desktop_capabilities`) for readiness checks.
- Keep `artifacts/` on monitored storage for auditability.

## 7. Release Packaging Command
Generate distributables + checksum manifest:
```powershell
.\.venv\Scripts\python.exe scripts\package_release.py
```
Outputs:
- `dist/*.whl`
- `dist/*.tar.gz`
- `artifacts/release/package_manifest_*.json`
- `artifacts/release/package_manifest_latest.json`


## 8. Strict Security Release Gate
```powershell
$env:NOVAFORGE_STRICT_SECURITY_GATE = '1'
.\.venv\Scripts\python.exe scripts\release_gate.py
```

Use `security-ignore.txt` to track explicit vulnerability exceptions (one ID per line) with documented rationale.

## 9. Strict Soak Release Gate
```powershell
$env:NOVAFORGE_STRICT_SOAK_GATE = '1'
$env:NOVAFORGE_SOAK_DURATION_SECONDS = '120'
$env:NOVAFORGE_SOAK_WORKERS = '3'
$env:NOVAFORGE_SOAK_MIN_PASS_RATE = '0.99'
$env:NOVAFORGE_SOAK_MIN_RUNS_PER_WORKER = '1'
.\.venv\Scripts\python.exe scripts\release_gate.py
```

Standalone soak run:
```powershell
.\.venv\Scripts\python.exe scripts\soak_sessions.py --duration-seconds 120 --workers 3 --preset core --min-pass-rate 0.99 --min-runs-per-worker 1
```
