from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from novaforge.config import load_settings
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run
from novaforge.state import STATE
from novaforge.tools.recording_tools import maybe_record

_MAX_READ_BYTES = 10 * 1024 * 1024  # 10 MB default cap


def register_file_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "File I/O operations on the local filesystem. "
            "action: read (read file contents), write (write/overwrite), append (append to file), "
            "list (list directory contents), exists (check path existence), "
            "delete (delete file), mkdir (create directory). "
            "Use encoding= to specify text encoding (default utf-8). "
            "max_bytes caps read size (default 10MB)."
        ),
    )
    def desktop_file(
        action: str,
        path: str,
        content: str | None = None,
        encoding: str = "utf-8",
        max_bytes: int = _MAX_READ_BYTES,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        act = action.strip().lower()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)
            p = Path(path)

            if act == "read":
                if not p.exists():
                    return err("desktop_file", f"File not found: {path}", started_ms=started, code="not_found")
                if not p.is_file():
                    return err("desktop_file", f"Not a file: {path}", started_ms=started, code="validation_error")
                size = p.stat().st_size
                if size > max(1, max_bytes):
                    return err("desktop_file", f"File too large ({size} bytes, max {max_bytes})", started_ms=started, code="validation_error")
                text = p.read_text(encoding=encoding)
                data = {"content": text, "size": size, "path": str(p.resolve()), "encoding": encoding}

            elif act == "write":
                if content is None:
                    return err("desktop_file", "content is required for write", started_ms=started, code="validation_error")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding=encoding)
                data = {"written": True, "size": len(content.encode(encoding)), "path": str(p.resolve())}

            elif act == "append":
                if content is None:
                    return err("desktop_file", "content is required for append", started_ms=started, code="validation_error")
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a", encoding=encoding) as f:
                    f.write(content)
                data = {"appended": True, "bytes_added": len(content.encode(encoding)), "path": str(p.resolve())}

            elif act == "list":
                if not p.exists():
                    return err("desktop_file", f"Directory not found: {path}", started_ms=started, code="not_found")
                if not p.is_dir():
                    return err("desktop_file", f"Not a directory: {path}", started_ms=started, code="validation_error")
                entries = []
                for child in sorted(p.iterdir()):
                    entries.append({
                        "name": child.name,
                        "is_dir": child.is_dir(),
                        "size": child.stat().st_size if child.is_file() else None,
                    })
                data = {"entries": entries, "count": len(entries), "path": str(p.resolve())}

            elif act == "exists":
                exists = p.exists()
                data = {
                    "exists": exists,
                    "is_file": p.is_file() if exists else None,
                    "is_dir": p.is_dir() if exists else None,
                    "path": str(p.resolve()),
                }

            elif act == "delete":
                if not p.exists():
                    return err("desktop_file", f"Path not found: {path}", started_ms=started, code="not_found")
                if p.is_file():
                    p.unlink()
                    data = {"deleted": True, "path": str(p.resolve())}
                else:
                    return err("desktop_file", "Use rmdir or shell for directory deletion", started_ms=started, code="validation_error")

            elif act == "mkdir":
                p.mkdir(parents=True, exist_ok=True)
                data = {"created": True, "path": str(p.resolve())}

            else:
                return err(
                    "desktop_file",
                    f"Unknown action: {action}",
                    hint="Use: read, write, append, list, exists, delete, mkdir",
                    started_ms=started,
                    code="validation_error",
                )

            maybe_record("desktop_file", {"action": act, "path": path}, data, session_id=resolved_session_id)
            return ok("desktop_file", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_file", str(e), started_ms=started)
