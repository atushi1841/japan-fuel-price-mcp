"""
RESTラッパー — japan-fuel-price-mcp の5ツールを素のGETエンドポイントでも公開する。

目的: RapidAPI等のOpenAPI基盤ゲートウェイはMCP（streamable-http）を直接叩けないため、
同じ純粋Python関数（src/fuel_price.py）をJSONで返す /rest/* ルートが生やして必要。

設計方針（kanban t_742cfd52）:
- FastMCP http_app（Starlette）に /rest/* と /openapi.json を同じアプリに載せる。
  /mcp ルートはそのままStandby MCPとして動作し続ける。
- Authorizationヘッダ検証はApifyプラットフォーム側が全ルートに実施済み（此处では検証しない）。
- PPE課金はMCP経路のみ（RESTはRapidAPI側の従量課金が収益源のため Actor.charge しない）。
- ハンドラは同期def — fuel_price がブロッキングI/O（urllib/openpyxl）なので
  Starletteがスレッドプールで実行してくれる。
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from src import fuel_price

logger = logging.getLogger(__name__)

_PRODUCTS = "premium | regular | diesel | kerosene_store | kerosene_delivery"


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _get_int(params: dict[str, str], key: str, default: int) -> int:
    raw = params.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"parameter '{key}' must be an integer, got {raw!r}") from None


# ──────────────────────────────────────────────
# GET /rest/latest
# ──────────────────────────────────────────────

def latest_handler(request) -> JSONResponse:
    q = dict(request.query_params)
    try:
        result = fuel_price.latest_price(q.get("prefecture") or "全国",
                                         q.get("product") or "regular")
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("rest latest failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


# ──────────────────────────────────────────────
# GET /rest/history
# ──────────────────────────────────────────────

def history_handler(request) -> JSONResponse:
    q = dict(request.query_params)
    try:
        result = fuel_price.price_history(q.get("prefecture") or "全国",
                                          q.get("product") or "regular",
                                          _get_int(q, "weeks", 12))
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("rest history failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


# ──────────────────────────────────────────────
# GET /rest/cheapest
# ──────────────────────────────────────────────

def cheapest_handler(request) -> JSONResponse:
    q = dict(request.query_params)
    try:
        result = fuel_price.cheapest_regions(q.get("product") or "regular",
                                             _get_int(q, "limit", 10))
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("rest cheapest failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


# ──────────────────────────────────────────────
# GET /rest/trend
# ──────────────────────────────────────────────

def trend_handler(request) -> JSONResponse:
    q = dict(request.query_params)
    try:
        result = fuel_price.national_trend(q.get("products") or "premium,regular,diesel",
                                           _get_int(q, "weeks", 52))
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("rest trend failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


# ──────────────────────────────────────────────
# GET /rest/regions
# ──────────────────────────────────────────────

def regions_handler(request) -> JSONResponse:
    try:
        result = fuel_price.list_regions()
    except Exception as e:  # noqa: BLE001
        logger.exception("rest regions failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


# ──────────────────────────────────────────────
# OpenAPIドキュメント（GET /openapi.json）
# ──────────────────────────────────────────────

BASE_URL = "https://fruitful-quintessence--japan-fuel-price-mcp.apify.actor"

OPENAPI_DOC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {
        "title": "Japan Fuel Price API",
        "version": "1.0.0",
        "x-category": "Data",
        "description": (
            "Official weekly retail fuel prices for Japan (47 prefectures + nationwide) from "
            "the METI Agency for Natural Resources and Energy Petroleum Products Price Survey. "
            "Premium/regular gasoline and diesel in JPY per liter, kerosene in JPY per 18L, "
            "tax-inclusive cash prices, updated every Wednesday. Latest price, up to 260 weeks "
            "of history, cheapest-prefecture ranking, and a nationwide multi-product trend "
            "(inflation indicator). Government Standard Terms of Use 2.0."
        ),
    },
    "servers": [{"url": BASE_URL}],
    "paths": {
        "/rest/latest": {
            "get": {
                "operationId": "getLatestFuelPrice",
                "summary": "Latest weekly fuel price (prefecture or nationwide)",
                "description": (
                    "Latest survey week's average retail cash price with week-over-week change "
                    "and the gasoline-tax component. Defaults: nationwide regular gasoline."
                ),
                "parameters": [
                    {"name": "prefecture", "in": "query", "required": False,
                     "description": "Kanji, romaji, or with 都/府/県 suffix (e.g. 東京, tokyo, 全国/nationwide)",
                     "schema": {"type": "string", "default": "全国"},
                     "example": "tokyo"},
                    {"name": "product", "in": "query", "required": False,
                     "description": f"One of: {_PRODUCTS}",
                     "schema": {"type": "string", "default": "regular",
                                "enum": ["premium", "regular", "diesel",
                                         "kerosene_store", "kerosene_delivery"]}},
                ],
                "responses": {"200": {"description": "Latest price object"}},
            }
        },
        "/rest/history": {
            "get": {
                "operationId": "getFuelPriceHistory",
                "summary": "Weekly fuel price history series",
                "description": "Up to 260 recent weeks of date/price series plus min/max/avg.",
                "parameters": [
                    {"name": "prefecture", "in": "query", "required": False,
                     "description": "Region name (kanji/romaji) or 全国/nationwide",
                     "schema": {"type": "string", "default": "全国"},
                     "example": "osaka"},
                    {"name": "product", "in": "query", "required": False,
                     "description": f"One of: {_PRODUCTS}",
                     "schema": {"type": "string", "default": "regular",
                                "enum": ["premium", "regular", "diesel",
                                         "kerosene_store", "kerosene_delivery"]}},
                    {"name": "weeks", "in": "query", "required": False,
                     "description": "Number of recent weeks (1-260)",
                     "schema": {"type": "integer", "default": 12, "minimum": 1, "maximum": 260}},
                ],
                "responses": {"200": {"description": "History object with series"}},
            }
        },
        "/rest/cheapest": {
            "get": {
                "operationId": "getCheapestPrefectures",
                "summary": "Cheapest / most expensive prefecture ranking",
                "description": "47 prefectures ranked by latest weekly average price (cheapest first, plus most expensive end).",
                "parameters": [
                    {"name": "product", "in": "query", "required": False,
                     "description": f"One of: {_PRODUCTS}",
                     "schema": {"type": "string", "default": "regular",
                                "enum": ["premium", "regular", "diesel",
                                         "kerosene_store", "kerosene_delivery"]}},
                    {"name": "limit", "in": "query", "required": False,
                     "description": "Entries per end of the ranking (1-47)",
                     "schema": {"type": "integer", "default": 10, "minimum": 1, "maximum": 47}},
                ],
                "responses": {"200": {"description": "Ranking object"}},
            }
        },
        "/rest/trend": {
            "get": {
                "operationId": "getNationalFuelTrend",
                "summary": "Nationwide multi-product weekly trend (inflation indicator)",
                "description": "Nationwide series per product with latest price, absolute change and % change over the window.",
                "parameters": [
                    {"name": "products", "in": "query", "required": False,
                     "description": "Comma-separated product keys",
                     "schema": {"type": "string", "default": "premium,regular,diesel"}},
                    {"name": "weeks", "in": "query", "required": False,
                     "description": "Lookback window in weeks (1-260)",
                     "schema": {"type": "integer", "default": 52, "minimum": 1, "maximum": 260}},
                ],
                "responses": {"200": {"description": "Trend object"}},
            }
        },
        "/rest/regions": {
            "get": {
                "operationId": "listFuelRegions",
                "summary": "List queryable regions and products (self-discovery)",
                "description": "47 prefectures + bureaus + nationwide with romaji aliases; 5 products with units; latest survey date.",
                "parameters": [],
                "responses": {"200": {"description": "Regions/products catalogue"}},
            }
        },
    },
}


def openapi_handler(request) -> JSONResponse:
    return JSONResponse(OPENAPI_DOC)


def rest_routes() -> list[BaseRoute]:
    """MCPアプリと同じStarletteに載せるRESTルート一式。"""
    return [
        Route("/openapi.json", openapi_handler, methods=["GET"]),
        Route("/rest/latest", latest_handler, methods=["GET"]),
        Route("/rest/history", history_handler, methods=["GET"]),
        Route("/rest/cheapest", cheapest_handler, methods=["GET"]),
        Route("/rest/trend", trend_handler, methods=["GET"]),
        Route("/rest/regions", regions_handler, methods=["GET"]),
    ]


def rest_app() -> Starlette:
    """単体起動可能なRESTアプリ（テスト用。本番ではmcp_appにroutes合成される）。"""
    return Starlette(routes=rest_routes())
