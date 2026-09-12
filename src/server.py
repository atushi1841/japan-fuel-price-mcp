"""
MCPサーバーファクトリー — Japan Fuel Price MCP.

資源エネルギー庁（METI）公式XLSX「給油所小売価格調査」の都道府県別・全国
週次平均現金価格（ハイオク/レギュラー/軽油/灯油）を、AIエージェント
（Claude, ChatGPT等）からMCPツールとして呼び出せるようにします。

get_server() がコントラクト関数。main.py がこれを呼び出し、
uvicorn 経由で Apify Standby モードでホスティングします。

データは初回呼び出し時にXLSXを取得・パースしてJSONキャッシュし、
以後はキャッシュから即答します（TTL 6時間で新週データを再チェック）。

課金は pay_per_event.json に定義された PPE イベント経由で行われます。
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from src import fuel_price

if os.environ.get("APIFY_CONTAINER_PORT"):
    from apify import Actor
else:
    from src.apify_shim import Actor  # type: ignore[assignment]

# ツールアノテーション（MCPクライアントへのヒント）— ローカルキャッシュ参照なので閉世界・読み取り専用
_READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_ATTRIBUTION = (
    "Source: Agency for Natural Resources and Energy (METI) "
    "Petroleum Products Price Survey, Government Standard Terms of Use 2.0."
)


def get_server() -> FastMCP:
    """
    FastMCP サーバーインスタンスを作成。
    この関数が main.py やテストから呼ばれる唯一のコントラクト。
    """
    server = FastMCP("japan-fuel-price-mcp", "0.1.0")

    # ──────────────────────────────────────────────
    # Tool 1: 最新価格
    # ──────────────────────────────────────────────
    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_latest_fuel_price(
        prefecture: str = "全国",
        product: str = "regular",
    ) -> dict:
        """Get the latest weekly average retail fuel price for a Japanese prefecture or nationwide.

        Official METI (Agency for Natural Resources and Energy) gasoline station survey, updated weekly. Prices are tax-inclusive cash prices: gasoline/diesel in JPY per liter, kerosene in JPY per 18L. Includes week-over-week change and the gasoline-tax component.

        Args:
            prefecture: prefecture name in kanji, romaji, or with 都/府/県 suffix (e.g. 東京, tokyo, 東京都, 全国/nationwide)
            product: premium | regular | diesel | kerosene_store | kerosene_delivery
        """
        await Actor.charge("fuel-price-latest")
        result = fuel_price.latest_price(prefecture, product)
        return {"type": "text", "text": _format_latest(result), "structuredContent": result}

    # ──────────────────────────────────────────────
    # Tool 2: 価格履歴
    # ──────────────────────────────────────────────
    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_fuel_price_history(
        prefecture: str = "全国",
        product: str = "regular",
        weeks: int = 12,
    ) -> dict:
        """Get weekly retail fuel price history for a Japanese prefecture or nationwide.

        Up to 260 recent weeks of prefecture-level data (official METI weekly survey). Useful for inflation tracking, cost analysis and trend charts. Returns date/price series plus min/max/avg.

        Args:
            prefecture: prefecture name (kanji or romaji), or 全国/nationwide
            product: premium | regular | diesel | kerosene_store | kerosene_delivery
            weeks: number of recent weeks (default 12, max 260)
        """
        await Actor.charge("fuel-price-history")
        result = fuel_price.price_history(prefecture, product, weeks)
        return {"type": "text", "text": _format_history(result), "structuredContent": result}

    # ──────────────────────────────────────────────
    # Tool 3: 安い都道府県ランキング
    # ──────────────────────────────────────────────
    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def cheapest_prefectures(
        product: str = "regular",
        limit: int = 10,
    ) -> dict:
        """Rank Japan's 47 prefectures by latest weekly average fuel price (cheapest first, plus most expensive).

        Official METI prefecture averages — the closest public proxy for "where is fuel cheapest in Japan". Great for travel cost planning and regional price-gap analysis.

        Args:
            product: premium | regular | diesel | kerosene_store | kerosene_delivery
            limit: how many entries for each end of the ranking (default 10, max 47)
        """
        await Actor.charge("fuel-price-cheapest")
        result = fuel_price.cheapest_regions(product, limit)
        return {"type": "text", "text": _format_cheapest(result), "structuredContent": result}

    # ──────────────────────────────────────────────
    # Tool 4: 全国トレンド（マクロ/インフレ指標）
    # ──────────────────────────────────────────────
    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def national_fuel_trend(
        products: str = "premium,regular,diesel",
        weeks: int = 52,
    ) -> dict:
        """Get nationwide weekly fuel price trend across multiple products — an inflation/macro indicator for Japan.

        Returns the series plus latest price, absolute change and % change over the window for each requested product.

        Args:
            products: comma-separated: premium,regular,diesel,kerosene_store,kerosene_delivery
            weeks: lookback window in weeks (default 52, max 260)
        """
        await Actor.charge("fuel-price-trend")
        result = fuel_price.national_trend(products, weeks)
        return {"type": "text", "text": _format_trend(result), "structuredContent": result}

    # ──────────────────────────────────────────────
    # Tool 5: 地域・製品リスト（自己発見用）
    # ──────────────────────────────────────────────
    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def list_fuel_regions() -> dict:
        """List all queryable regions (47 prefectures + regional bureaus + nationwide) with romaji aliases, and the 5 fuel products with their units.

        Call this first if unsure which region/product names are valid.

        Returns:
            Regions with kanji + romaji names, product keys with labels and units, latest survey date.
        """
        await Actor.charge("fuel-price-latest")
        result = fuel_price.list_regions()
        lines = [f"Latest survey date: {result['latest_survey_date']}", ""]
        lines.append("Products:")
        for k, v in result["products"].items():
            lines.append(f"  {k}: {v['label']} ({v['unit']})")
        lines.append("")
        lines.append("Prefectures (name / romaji):")
        lines.append("  " + ", ".join(f"{p['name']}={p['romaji']}" for p in result["prefectures"]))
        lines.append("")
        lines.append(f"National: 全国=nationwide. Bureaus: {', '.join(b['name'] for b in result['bureaus'])}")
        lines.append("")
        lines.append(_ATTRIBUTION)
        return {"type": "text", "text": "\n".join(lines), "structuredContent": result}

    return server


# ──────────────────────────────────────────────
# テキスト整形
# ──────────────────────────────────────────────

def _format_latest(r: dict) -> str:
    wow = r.get("week_over_week")
    wow_s = f"{wow:+.1f}" if wow is not None else "n/a"
    lines = [
        f"{r['region']} ({r['region_romaji']}) — {r['product_label']}",
        f"  {r['price']} {r['unit']} (survey week {r['survey_date']}, tax-inclusive cash price)",
        f"  previous week: {r['previous_price']} ({r['previous_date']}), WoW change: {wow_s}",
    ]
    if r.get("gas_tax_component") is not None:
        lines.append(f"  gasoline-tax component: {r['gas_tax_component']} JPY/L")
    lines.append("")
    lines.append(_ATTRIBUTION)
    return "\n".join(lines)


def _format_history(r: dict) -> str:
    lines = [
        f"{r['region']} — {r['product']} weekly history ({r['weeks_returned']} weeks, {r['unit']})",
        f"  min {r['min']} / max {r['max']} / avg {r['avg']}",
    ]
    for s in r["series"][-8:]:
        lines.append(f"  {s['date']}: {s['price']}")
    if r["weeks_returned"] > 8:
        lines.append(f"  ... ({r['weeks_returned'] - 8} earlier weeks in structuredContent)")
    lines.append("")
    lines.append(_ATTRIBUTION)
    return "\n".join(lines)


def _format_cheapest(r: dict) -> str:
    lines = [
        f"Cheapest prefectures — {r['product']} (survey week {r['survey_date']}, {r['unit']})",
        f"  national average: {r['national_average']}",
    ]
    for i, row in enumerate(r["cheapest"], 1):
        lines.append(f"  {i}. {row['prefecture']} ({row['romaji']}): {row['price']}")
    lines.append("")
    lines.append("Most expensive:")
    for i, row in enumerate(r["most_expensive"], 1):
        lines.append(f"  {i}. {row['prefecture']} ({row['romaji']}): {row['price']}")
    lines.append("")
    lines.append(_ATTRIBUTION)
    return "\n".join(lines)


def _format_trend(r: dict) -> str:
    lines = [f"Nationwide fuel trend — last {r['weeks']} weeks ({r['unit_note']})"]
    for pk, info in r["latest"].items():
        lines.append(
            f"  {pk}: {info['price']} ({info['survey_date']}), "
            f"change {info['change_vs_window_start']:+.1f} ({info['pct_change']:+.2f}%) over window"
        )
    lines.append("")
    lines.append(_ATTRIBUTION)
    return "\n".join(lines)
