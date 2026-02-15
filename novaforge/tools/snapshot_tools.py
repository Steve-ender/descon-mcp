from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from novaforge.config import load_settings
from novaforge.engines.window_engine import WINDOW_ENGINE
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run
from novaforge.state import STATE


def _format_tree(node: dict[str, Any], indent: int = 0, ref_counter: list[int] | None = None) -> list[str]:
    """Format a UI Automation tree node as compact, LLM-readable text lines.

    Each interactive element gets a ref like [ref1], [ref2], etc.
    Output resembles Playwright's browser_snapshot format.
    """
    if ref_counter is None:
        ref_counter = [0]

    lines: list[str] = []
    prefix = "  " * indent

    title = (node.get("title") or "").strip()
    control_type = (node.get("control_type") or "").strip()
    auto_id = (node.get("automation_id") or "").strip()
    rect = node.get("rect")

    if node.get("truncated"):
        lines.append(f"{prefix}... ({node.get('total', '?')} children, {node.get('shown', '?')} shown)")
        return lines

    # Assign ref to interactive elements
    ref_counter[0] += 1
    ref = f"e{ref_counter[0]}"

    # Build compact description
    parts: list[str] = []
    if control_type:
        parts.append(control_type)
    if title:
        parts.append(f'"{title}"')
    if auto_id:
        parts.append(f"[id={auto_id}]")
    if rect:
        cx = rect.get("center_x", "?")
        cy = rect.get("center_y", "?")
        parts.append(f"@({cx},{cy})")

    desc = " ".join(parts) if parts else "(empty)"
    lines.append(f"{prefix}[{ref}] {desc}")

    for child in node.get("children", []):
        lines.extend(_format_tree(child, indent + 1, ref_counter))

    return lines


def register_snapshot_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Get the UI Automation accessibility tree as structured text — like Playwright's browser_snapshot but for the Windows desktop. "
            "Returns all interactive elements with types, labels, automation IDs, and click coordinates. "
            "Much cheaper and more reliable than screenshot analysis for understanding what's on screen. "
            "Target a specific window with title_regex, window_handle, or window_pid. "
            "Increase max_depth (default 4) and max_children (default 50) to see more of deep UIs."
        ),
    )
    def desktop_ui_snapshot(
        title_regex: str | None = None,
        window_handle: int | None = None,
        window_pid: int | None = None,
        max_depth: int = 4,
        max_children: int = 50,
        timeout_ms: int = 5000,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)

            tree_data = WINDOW_ENGINE.element_tree(
                title_regex=title_regex,
                window_handle=window_handle,
                window_pid=window_pid,
                max_depth=max_depth,
                max_children=max_children,
                timeout_ms=timeout_ms,
            )

            lines = _format_tree(tree_data.get("tree", {}))
            snapshot_text = "\n".join(lines)

            data = {
                "window_title": tree_data.get("window_title", ""),
                "window_handle": tree_data.get("window_handle"),
                "window_pid": tree_data.get("window_pid"),
                "max_depth": max_depth,
                "max_children": max_children,
                "element_count": len(lines),
                "snapshot": snapshot_text,
            }
            return ok("desktop_ui_snapshot", data=data, started_ms=started)
        except Exception as e:
            return err("desktop_ui_snapshot", str(e), started_ms=started)
