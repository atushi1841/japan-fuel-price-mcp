"""RESTラッパー（src/rest.py）のインプロセステスト。

fuel_price は network-dependent なので monkeypatch で純粋関数をスタブし、
Starlette TestClient でルート配線・パラメータ解析・エラーコードを検証する。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("FUEL_DATA_DIR", str(ROOT / "tests" / "_tmp_cache"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import rest  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    # fuel_price 側をスタブ（ネットワーク禁止）
    from src import fuel_price

    def fake_latest(prefecture="全国", product="regular"):
        if product not in ("premium", "regular", "diesel", "kerosene_store", "kerosene_delivery"):
            raise ValueError(f"unknown product: {product}")
        return {"region": prefecture, "product": product, "price": 180.5}

    def fake_history(prefecture="全国", product="regular", weeks=12):
        return {"region": prefecture, "weeks_returned": weeks, "series": []}

    def fake_cheapest(product="regular", limit=10):
        return {"product": product, "cheapest": [{"prefecture": "高知"} for _ in range(limit)]}

    def fake_trend(products="premium,regular,diesel", weeks=52):
        return {"weeks": weeks, "products": products}

    def fake_regions():
        return {"prefectures": [{"name": "東京", "romaji": "tokyo"}] * 47}

    monkeypatch.setattr(fuel_price, "latest_price", fake_latest)
    monkeypatch.setattr(fuel_price, "price_history", fake_history)
    monkeypatch.setattr(fuel_price, "cheapest_regions", fake_cheapest)
    monkeypatch.setattr(fuel_price, "national_trend", fake_trend)
    monkeypatch.setattr(fuel_price, "list_regions", fake_regions)
    return TestClient(rest.rest_app())


def test_openapi_json_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    doc = r.json()
    assert doc["info"]["x-category"] == "Data"
    assert set(doc["paths"]) == {
        "/rest/latest", "/rest/history", "/rest/cheapest", "/rest/trend", "/rest/regions",
    }
    for p in doc["paths"].values():
        assert "get" in p and "operationId" in p["get"]


def test_rest_latest_defaults(client):
    r = client.get("/rest/latest")
    assert r.status_code == 200
    assert r.json()["region"] == "全国" and r.json()["price"] == 180.5


def test_rest_latest_params(client):
    r = client.get("/rest/latest", params={"prefecture": "tokyo", "product": "regular"})
    assert r.status_code == 200 and r.json()["region"] == "tokyo"


def test_rest_latest_region_alias(client):
    """region は prefecture の別名として効く（RapidAPIコンシューマが region と書くケース）。"""
    r = client.get("/rest/latest", params={"region": "tokyo"})
    assert r.status_code == 200 and r.json()["region"] == "tokyo"


def test_rest_latest_prefecture_wins_over_region(client):
    r = client.get("/rest/latest", params={"prefecture": "osaka", "region": "tokyo"})
    assert r.status_code == 200 and r.json()["region"] == "osaka"


def test_rest_history_region_alias(client):
    r = client.get("/rest/history", params={"region": "tokyo", "weeks": "4"})
    assert r.status_code == 200
    assert r.json()["region"] == "tokyo" and r.json()["weeks_returned"] == 4


def test_openapi_documents_region_alias(client):
    doc = client.get("/openapi.json").json()
    for path in ("/rest/latest", "/rest/history"):
        names = {p["name"] for p in doc["paths"][path]["get"]["parameters"]}
        assert {"prefecture", "region"} <= names, path


def test_rest_latest_bad_product_400(client):
    r = client.get("/rest/latest", params={"product": "vodka"})
    assert r.status_code == 400 and "error" in r.json()


def test_rest_history_weeks(client):
    r = client.get("/rest/history", params={"weeks": "30"})
    assert r.status_code == 200 and r.json()["weeks_returned"] == 30


def test_rest_history_bad_weeks_400(client):
    r = client.get("/rest/history", params={"weeks": "abc"})
    assert r.status_code == 400


def test_rest_cheapest_limit(client):
    r = client.get("/rest/cheapest", params={"limit": "3"})
    assert r.status_code == 200 and len(r.json()["cheapest"]) == 3


def test_rest_trend_products(client):
    r = client.get("/rest/trend", params={"products": "regular,diesel", "weeks": "8"})
    assert r.status_code == 200
    assert r.json()["products"] == "regular,diesel" and r.json()["weeks"] == 8


def test_rest_regions(client):
    r = client.get("/rest/regions")
    assert r.status_code == 200 and len(r.json()["prefectures"]) == 47


def test_upstream_failure_503(client, monkeypatch):
    from src import fuel_price

    def boom(*a, **k):
        raise RuntimeError("METI xlsx down")

    monkeypatch.setattr(fuel_price, "latest_price", boom)
    r = client.get("/rest/latest")
    assert r.status_code == 503 and "unavailable" in r.json()["error"]


def test_main_wires_rest_routes_into_http_app():
    """main.py の合成契約: get_server().http_app() + rest_routes() で /rest/latest が活着すること。"""
    from src.server import get_server

    server = get_server()
    app = server.http_app(transport="streamable-http")
    app.router.routes.extend(rest.rest_routes())
    # ルート表に /rest/latest と /openapi.json が存在すること（呼ばない＝課金キャッシュに触らない）
    paths = {getattr(rt, "path", None) for rt in app.router.routes}
    assert "/rest/latest" in paths and "/openapi.json" in paths
