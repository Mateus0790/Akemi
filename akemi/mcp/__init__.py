"""MCP: a Akemi usa as ferramentas de outros aplicativos (docs/MCP.md)."""

from .client import MCPError, MCPSession, Tool
from .hub import MCPHub, ServerConfig, load_config, parse_config, parse_paths

__all__ = ["MCPError", "MCPHub", "MCPSession", "ServerConfig", "Tool", "load_config", "parse_config", "parse_paths"]
