"""Render docs/tools.svg: the tool list this server actually advertises over MCP.

Connects in-process with the SDK client and calls tools/list. No upstream
source is contacted.

    python scripts/render_tool_list.py
"""

import asyncio
from pathlib import Path

from mcp.client import Client
from rich.console import Console
from rich.table import Table

from indian_markets_mcp.server import mcp

OUT = Path(__file__).resolve().parents[1] / "docs" / "tools.svg"


async def main() -> None:
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools

    table = Table(title=f"tools/list -> {len(tools)} tools", title_justify="left", show_lines=True)
    table.add_column("name", style="bold cyan", no_wrap=True)
    table.add_column("title")
    table.add_column("read-only", justify="center")
    table.add_column("description (first sentence)")
    for tool in tools:
        read_only = bool(tool.annotations and tool.annotations.read_only_hint)
        first = (tool.description or "").split(". ")[0].rstrip(".") + "."
        table.add_row(tool.name, tool.title or "", "yes" if read_only else "no", first)

    console = Console(record=True, width=120)
    console.print(table)
    console.save_svg(str(OUT), title="python scripts/render_tool_list.py")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
