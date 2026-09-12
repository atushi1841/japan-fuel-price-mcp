"""japan-fuel-price-mcp コアパーサー／クエリAPIのテスト（ネットワーク不要・シードキャッシュ利用）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# テストはワークキャッシュを使わずシードにフォールバックさせる
os.environ["FUEL_DATA_DIR"] = str(ROOT / "tests" / "_tmp_cache")

from src import fuel_price  # noqa: E402

# モジュールレベルでネットワークをスタブ化: 全テストはシードキャッシュのみで動く（速い・決定的）
fuel_price._http_get = lambda url, timeout=90: None  # type: ignore[assignment]
fuel_price._recent_failure = lambda: True  # type: ignore[assignment]


def _seed_only() -> dict:
    """シードキャッシュのみでデータを得る（ワークキャッシュ除去→fetchスキップ→シードフォールバック）。"""
    cache = fuel_price.cache_path()
    if os.path.exists(cache):
        os.remove(cache)
    return fuel_price.fetch_latest()


def test_seed_cache_exists_and_parses():
    data = _seed_only()
    assert set(data["products"]) == {"premium", "regular", "diesel", "kerosene_store", "kerosene_delivery"}
    assert len(data["prefectures"]) == 47, data["prefectures"]
    assert data["latest_date"] and data["latest_date"] >= "2026-01-01"


def test_normalize_region():
    assert fuel_price.normalize_region("東京都") == "東京"
    assert fuel_price.normalize_region("tokyo") == "東京"
    assert fuel_price.normalize_region("TOKYO") == "東京"
    assert fuel_price.normalize_region("神奈川") == "神奈川"
    assert fuel_price.normalize_region("全国") == "全国"
    assert fuel_price.normalize_region("nationwide") == "全国"
    assert fuel_price.normalize_region("大阪府") == "大阪"
    assert fuel_price.normalize_region("  鹿児島  ") == "鹿児島"


def test_latest_price_nationwide():
    r = fuel_price.latest_price("全国", "regular")
    assert r["product"] == "regular"
    assert r["unit"] == "JPY/liter"
    assert 100 < r["price"] < 300, r  # 全国レギュラーは現実的な範囲
    assert r["survey_date"] >= "2026-01-01"
    assert r["week_over_week"] is None or abs(r["week_over_week"]) < 20


def test_latest_price_prefecture_and_kerosene_unit():
    r = fuel_price.latest_price("東京", "premium")
    assert r["region_romaji"] == "tokyo"
    k = fuel_price.latest_price("北海道", "kerosene_store")
    assert k["unit"] == "JPY/18L"
    assert 900 < k["price"] < 3000, k


def test_latest_price_unknown_region_raises():
    try:
        fuel_price.latest_price("月面", "regular")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_price_history():
    r = fuel_price.price_history("全国", "diesel", weeks=30)
    assert r["weeks_returned"] == 30
    dates = [s["date"] for s in r["series"]]
    assert dates == sorted(dates)
    assert r["min"] <= r["avg"] <= r["max"]


def test_price_history_prefecture_recent_only():
    r = fuel_price.price_history("愛知", "regular", weeks=52)
    assert r["weeks_returned"] == 52


def test_cheapest_regions():
    r = fuel_price.cheapest_regions("regular", 10)
    assert len(r["cheapest"]) == 10
    prices = [row["price"] for row in r["cheapest"]]
    assert prices == sorted(prices)
    names = {row["prefecture"] for row in r["cheapest"]}
    bureau_names = set(fuel_price.BUREAUS)
    assert not (names & bureau_names), "地方局はランキングに含めない"
    assert r["most_expensive"][0]["price"] >= r["cheapest"][-1]["price"]


def test_national_trend():
    r = fuel_price.national_trend("premium,regular,diesel", weeks=52)
    assert set(r["latest"]) == {"premium", "regular", "diesel"}
    for pk in ("premium", "regular", "diesel"):
        assert r["latest"][pk]["price"] > 0
        assert abs(r["latest"][pk]["pct_change"]) < 100


def test_product_alias():
    assert fuel_price._product_key("ガソリン") == "regular"
    assert fuel_price._product_key("kerosene") == "kerosene_store"
    try:
        fuel_price._product_key("lpg")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_list_regions():
    r = fuel_price.list_regions()
    assert len(r["prefectures"]) == 47
    assert r["national"]["romaji"] == "nationwide"
    assert "premium" in r["products"]
