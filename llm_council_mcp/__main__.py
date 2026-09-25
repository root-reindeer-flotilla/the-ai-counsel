"""Entry point for the LLM Council Plus MCP server."""

import argparse
import asyncio
import sys

from .server import create_server, run_stdio, run_sse


def main():
    parser = argparse.ArgumentParser(
        description="LLM Council Plus MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local backend, stdio transport (for Claude Code / Gemini CLI)
  python -m llm_council_mcp

  # Remote backend, stdio transport
  python -m llm_council_mcp --base-url https://yourserver.com:8001

  # Standalone SSE transport fallback (Note: SSE is built into the main uvicorn app at /mcp/sse on port 8001!)
  python -m llm_council_mcp --transport sse --port 8002
        """,
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Base URL of the LLM Council Plus backend (default: http://localhost:8001)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode: stdio (default) or sse",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8002,
        help="Port for SSE transport (default: 8002)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host for SSE transport (default: 0.0.0.0)",
    )
    args = parser.parse_args()

    # host/port must be passed to create_server because FastMCP reads them
    # from its settings object (set at construction time).
    server = create_server(
        base_url=args.base_url,
        host=args.host,
        port=args.port,
    )

    if args.transport == "sse":
        asyncio.run(run_sse(server, host=args.host, port=args.port))
    else:
        asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
