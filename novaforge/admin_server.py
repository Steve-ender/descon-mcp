from __future__ import annotations

from fastmcp import FastMCP

from novaforge.tools.health_tools import register_health_tools
from novaforge.tools.recording_tools import register_recording_tools
from novaforge.tools.session_tools import register_session_tools
from novaforge.tools.transaction_tools import register_transaction_tools
from novaforge.tools.vision_tools import register_vision_admin_tools

mcp = FastMCP(
    "NovaForge Admin",
    instructions=(
        "NovaForge Admin MCP: Infrastructure tools for desktop automation sessions.\n\n"
        "## Tools\n"
        "- **desktop_session**: start/stop/status/pause/resume/cancel/emergency_stop/policy\n"
        "- **desktop_transaction**: start/checkpoint/rollback_hint/end/status/export\n"
        "- **desktop_recording**: start/stop/status/get/clear/save/load/replay/to_plan/plan_normalize/plan_diff/plan_compile\n"
        "- **desktop_health**: capabilities/healthcheck/diagnose/failure_envelope/broker_ping/broker_stop\n"
        "- **desktop_find_template**: OpenCV template matching\n\n"
        "These tools manage the automation framework. For actual desktop interaction, use the core NovaForge MCP server."
    ),
)

register_session_tools(mcp)
register_transaction_tools(mcp)
register_recording_tools(mcp)
register_health_tools(mcp)
register_vision_admin_tools(mcp)


def main() -> None:
    mcp.run(transport="stdio", show_banner=False, log_level="ERROR")


if __name__ == "__main__":
    main()
