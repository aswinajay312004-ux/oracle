"""Starts the local read-only MCP server (backend/mcp_server.py) on stdio. Example client configuration
(Claude Code, .mcp.json in the project folder on the laptop):

    {"mcpServers": {"poppys-erp": {"command": ".venv\\\\Scripts\\\\python.exe", "args": ["scripts\\\\mcp_server.py"]}}}
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.mcp_server import main  # noqa: E402

if __name__ == '__main__':
    main()
