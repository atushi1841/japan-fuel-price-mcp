"""MCP HTTPスモーク — streamable-httpクライアントで initialize/tools list/tools call を確認。

Usage: python3 tests/http_smoke.py [http://localhost:3999/mcp]
"""

from __future__ import annotations

import asyncio
import sys

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3999/mcp"

PASS = 0
FAIL = 0


def green(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  PASS  {msg}")


def red(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")


async def main() -> int:
    print(f"MCP HTTP smoke — {BASE_URL}")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url=BASE_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout=30)
            name = getattr(init, "serverInfo", None)
            if name and name.name == "japan-fuel-price-mcp":
                green(f"initialize → {name.name} {name.version}")
            else:
                red(f"initialize → {name}")

            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            names = {t.name for t in tools.tools}
            expected = {
                "get_latest_fuel_price", "get_fuel_price_history",
                "cheapest_prefectures", "national_fuel_trend", "list_fuel_regions",
            }
            if names == expected:
                green(f"tools/list → {len(names)} tools")
            else:
                red(f"tools/list → {names}")

            res = await asyncio.wait_for(
                session.call_tool("get_latest_fuel_price",
                                 {"prefecture": "大阪", "product": "regular"}),
                timeout=60,
            )
            text = "".join(c.text for c in res.content if hasattr(c, "text"))
            if "大阪" in text and "JPY/liter" in text and "2026-" in text:
                green("tools/call get_latest_fuel_price → real data")
                print("  ----")
                for line in text.splitlines():
                    print("  " + line)
                print("  ----")
            else:
                red(f"tools/call content unexpected: {text[:200]}")

            res2 = await asyncio.wait_for(
                session.call_tool("national_fuel_trend", {"products": "regular,diesel", "weeks": 8}),
                timeout=60,
            )
            text2 = "".join(c.text for c in res2.content if hasattr(c, "text"))
            if "regular" in text2 and "diesel" in text2:
                green("tools/call national_fuel_trend → ok")
            else:
                red(f"trend unexpected: {text2[:200]}")

    print(f"Results: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
