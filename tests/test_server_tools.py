"""MCPサーバーのツール登録・インプロセス呼び出しスモークテスト。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["FUEL_DATA_DIR"] = str(ROOT / "tests" / "_tmp_cache")

from src import fuel_price  # noqa: E402

fuel_price._http_get = lambda url, timeout=90: None  # type: ignore[assignment]
fuel_price._recent_failure = lambda: True  # type: ignore[assignment]

from src.server import get_server  # noqa: E402


def test_server_registers_five_tools():
    server = get_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "get_latest_fuel_price",
        "get_fuel_price_history",
        "cheapest_prefectures",
        "national_fuel_trend",
        "list_fuel_regions",
    }, names


def test_call_latest_tool():
    server = get_server()
    res = asyncio.run(
        server.call_tool("get_latest_fuel_price", {"prefecture": "東京", "product": "regular"})
    )
    payload = res.structured_content if hasattr(res, "structured_content") else res
    text = payload["text"] if isinstance(payload, dict) else str(payload)
    assert "東京" in text and "JPY/liter" in text


def test_call_cheapest_tool():
    server = get_server()
    res = asyncio.run(server.call_tool("cheapest_prefectures", {"product": "diesel", "limit": 5}))
    payload = res.structured_content if hasattr(res, "structured_content") else res
    sc = payload["structuredContent"] if isinstance(payload, dict) and "structuredContent" in payload else payload
    assert isinstance(sc, dict) and len(sc["cheapest"]) == 5
