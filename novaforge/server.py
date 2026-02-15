from __future__ import annotations

from fastmcp import FastMCP

from novaforge.tools.action_tools import register_action_tools
from novaforge.tools.file_tools import register_file_tools
from novaforge.tools.input_tools import register_input_tools
from novaforge.tools.notify_tools import register_notify_tools
from novaforge.tools.inspect_tools import register_inspect_tools
from novaforge.tools.orchestration_tools import register_orchestration_tools
from novaforge.tools.shell_tools import register_shell_tools
from novaforge.tools.snapshot_tools import register_snapshot_tools
from novaforge.tools.vision_tools import register_vision_tools
from novaforge.tools.wait_tools import register_wait_tools
from novaforge.tools.window_tools import register_window_tools

mcp = FastMCP(
    "NovaForge MCP",
    instructions=(
        "NovaForge MCP: Full Windows desktop automation.\n\n"
        "## Quick Start\n"
        "Sessions auto-start on your first tool call — no setup needed.\n"
        "1. Call desktop_screenshot() or desktop_ui_snapshot() to SEE the screen.\n"
        "2. Use desktop_click(x, y), desktop_type(text), desktop_scroll(clicks), desktop_key(keys=[...]) to interact.\n"
        "3. Call desktop_screenshot() again to verify the result.\n\n"
        "## Core Workflow: See → Act → Verify\n"
        "Always observe the screen first. Then act. Then verify. "
        "This loop is the foundation of reliable automation.\n\n"
        "## Perception (see the screen)\n"
        "- **desktop_screenshot()** — see the screen as an image (returned inline)\n"
        "- **desktop_ui_snapshot()** — get the accessibility tree as structured text with element types, labels, and coordinates\n"
        "- **desktop_observe()** — combined snapshot: windows + cursor + monitors + DPI + screenshot in one call\n"
        "- **desktop_read_text(image_path)** — extract text from an image via OCR\n\n"
        "## Actions (interact with the computer)\n"
        "- **desktop_click(x, y)** — click at coordinates; or desktop_click(text='Save') to find text via OCR and click it\n"
        "- **desktop_type(text)** — type text at the cursor (supports Unicode)\n"
        "- **desktop_key(keys=['ctrl','c'])** — keyboard shortcuts; desktop_key(key='enter') for single keys\n"
        "- **desktop_scroll(clicks)** — scroll wheel (positive=up, negative=down)\n"
        "- **desktop_mouse(action='move'|'drag'|'position'|'down'|'up')** — advanced mouse control\n"
        "- **desktop_clipboard(action='read'|'write')** — clipboard read/write\n\n"
        "## Waiting & Synchronization\n"
        "- **desktop_wait(time_ms=2000)** — simple sleep\n"
        "- **desktop_wait(until='screen_stable')** — wait until screen pixels stop changing\n"
        "- **desktop_wait(until='text_visible', text='Done')** — wait until text appears on screen\n"
        "- **desktop_wait(until='window_exists', title_regex='Notepad')** — wait for a window\n\n"
        "## Smart Interaction\n"
        "- **desktop_window(action='list'|'focus'|'bind'|'control'|'launch'|'close'|...)** — window management\n"
        "- **desktop_element(action='query'|'act'|'wait_for'|'assert')** — Windows UI Automation for reliable element interaction\n"
        "- **desktop_shell(command)** — run shell commands (PowerShell, cmd, bash)\n\n"
        "## Batch Execution (speed)\n"
        "- **desktop_act(plan=[...])** — execute multiple steps in one call without LLM round-trips. "
        "Supports: click, type, key, wait, screenshot, read_text, launch_app, scroll, and more.\n\n"
        "## Tips\n"
        "- Use desktop_act() for multi-step sequences to avoid per-action latency.\n"
        "- Use desktop_ui_snapshot() to understand UI structure without vision model round-trips.\n"
        "- Use desktop_click(text='Save') when you know the text label — no need to screenshot first.\n"
        "- Use desktop_window(action='bind', title_regex=...) to set a default window target.\n"
        "- Use desktop_wait(until='screen_stable') after actions that trigger animations.\n\n"
        "## Admin Tools\n"
        "Session management, transactions, recordings, health diagnostics, and template matching "
        "are available in the separate NovaForge Admin MCP server (novaforge-admin)."
    ),
)

register_window_tools(mcp)
register_input_tools(mcp)
register_vision_tools(mcp)
register_inspect_tools(mcp)
register_action_tools(mcp)
register_snapshot_tools(mcp)
register_wait_tools(mcp)
register_shell_tools(mcp)
register_file_tools(mcp)
register_notify_tools(mcp)
register_orchestration_tools(mcp)


def main() -> None:
    mcp.run(transport="stdio", show_banner=False, log_level="ERROR")


if __name__ == "__main__":
    main()
