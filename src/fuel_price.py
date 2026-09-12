"""
燃料価格データコア — 資源エネルギー庁 給油所小売価格調査 公式XLSXパーサー。

ソース: https://www.enecho.meti.go.jp/statistics/petroleum_and_lpgas/pl007/
  - xlsx/YYMMDDs5.xlsx = 都道府県別・全国週次平均現金価格（1990年〜、毎週更新）
  - シート: ハイオク / レギュラー / 軽油 / 灯油店頭 / 灯油配達
  - 列: 調査日, 全国, 47都道府県 + 9地方局, （うちガソリン税）, 消費税率
  - 単位: ガソリン・軽油 = 円/L、灯油 = 円/18L（消費税込み現金価格）

利用規約: 政府標準利用規約2.0（出典明示で商用可）

このモジュールは:
1. 直近N日分のYYMMDDs5.xlsx候補URLを順にGET（ブラウザUA必須、403対策）
2. openpyxl(read_only)でパース → 週次レコードのリストに変換
3. JSONキャッシュ（FUEL_DATA_DIR、コンテナ内は /tmp）に保存、以後はキャッシュ優先
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = "https://www.enecho.meti.go.jp/statistics/petroleum_and_lpgas/pl007/xlsx/{ymd}s5.xlsx"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# シート名 → 製品キー
PRODUCT_SHEETS: dict[str, str] = {
    "ハイオク": "premium",
    "レギュラー": "regular",
    "軽油": "diesel",
    "灯油店頭": "kerosene_store",
    "灯油配達": "kerosene_delivery",
}

PRODUCT_LABELS: dict[str, str] = {
    "premium": "ハイオク (premium gasoline)",
    "regular": "レギュラー (regular gasoline)",
    "diesel": "軽油 (diesel)",
    "kerosene_store": "灯油店頭 (kerosene, store pickup)",
    "kerosene_delivery": "灯油配達 (kerosene, delivery)",
}

PRODUCT_UNITS: dict[str, str] = {
    "premium": "JPY/liter",
    "regular": "JPY/liter",
    "diesel": "JPY/liter",
    "kerosene_store": "JPY/18L",
    "kerosene_delivery": "JPY/18L",
}

# 地方局・集計列（47都道府県ランキングからは除外）
# 北海道・沖縄はMETI調査では「局」単位で集計されるため、都道府県扱いに正規化する
BUREAUS = {
    "北海道局", "東北局", "関東局", "中部局", "近畿局",
    "中国局", "四国局", "九州局", "沖縄局", "九州沖縄局",
}
# 都道府県として扱う局（本州以北の局は地方集計なので除外のまま）
PREF_BUREAUS = {"北海道局": "北海道", "沖縄局": "沖縄"}

# 47都道府県 + 全国 + 地方局 → ローマ字エイリアス（AIエージェント入力吸収用）
ROMAJI: dict[str, str] = {
    "全国": "nationwide",
    "北海道": "hokkaido", "青森": "aomori", "岩手": "iwate", "宮城": "miyagi",
    "秋田": "akita", "山形": "yamagata", "福島": "fukushima", "茨城": "ibaraki",
    "栃木": "tochigi", "群馬": "gunma", "埼玉": "saitama", "千葉": "chiba",
    "東京": "tokyo", "神奈川": "kanagawa", "新潟": "niigata", "富山": "toyama",
    "石川": "ishikawa", "福井": "fukui", "山梨": "yamanashi", "長野": "nagano",
    "岐阜": "gifu", "静岡": "shizuoka", "愛知": "aichi", "三重": "mie",
    "滋賀": "shiga", "京都": "kyoto", "大阪": "osaka", "兵庫": "hyogo",
    "奈良": "nara", "和歌山": "wakayama", "鳥取": "tottori", "島根": "shimane",
    "岡山": "okayama", "広島": "hiroshima", "山口": "yamaguchi", "徳島": "tokushima",
    "香川": "kagawa", "愛媛": "ehime", "高知": "kochi", "福岡": "fukuoka",
    "佐賀": "saga", "長崎": "nagasaki", "熊本": "kumamoto", "大分": "oita",
    "宮崎": "miyazaki", "鹿児島": "kagoshima", "沖縄": "okinawa",
    "北海道局": "hokkaido-bureau", "東北局": "tohoku-bureau", "関東局": "kanto-bureau",
    "中部局": "chubu-bureau", "近畿局": "kinki-bureau", "中国局": "chugoku-bureau",
    "四国局": "shikoku-bureau", "九州局": "kyushu-bureau", "沖縄局": "okinawa-bureau",
    "九州沖縄局": "kyushu-okinawa-bureau",
}
ROMAJI_TO_JA: dict[str, str] = {v: k for k, v in ROMAJI.items()}

# キャッシュに保持する直近週数（都道府県別）。全国は全期間保持する。
RETENTION_WEEKS = 260

CACHE_TTL_SECONDS = 6 * 3600  # 6時間ごとに新ファイル有無を再チェック
FETCH_FAIL_BACKOFF = 3600     # 取得失敗後1時間は再試行しない（レート制限対策）

# ビルド時に同梱されるシードキャッシュ（Apifyコンテナの初回・METI遮断時のフォールバック）
SEED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fuel_prices.json")


def _data_dir() -> str:
    d = os.environ.get("FUEL_DATA_DIR") or os.path.join(
        os.environ.get("APIFY_ACTOR_STORAGE_DIR", tempfile.gettempdir()), "fuel-cache"
    )
    os.makedirs(d, exist_ok=True)
    return d


def cache_path() -> str:
    return os.path.join(_data_dir(), "fuel_prices.json")


def _fail_marker() -> str:
    return os.path.join(_data_dir(), "fuel_fetch_fail.json")


def _recent_failure() -> bool:
    try:
        with open(_fail_marker(), encoding="utf-8") as f:
            return datetime.now().timestamp() - json.load(f).get("epoch", 0) < FETCH_FAIL_BACKOFF
    except Exception:  # noqa: BLE001
        return False


def _mark_failure() -> None:
    try:
        with open(_fail_marker(), "w", encoding="utf-8") as f:
            json.dump({"epoch": datetime.now().timestamp()}, f)
    except Exception:  # noqa: BLE001
        pass


def _load_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def normalize_region(name: str) -> str:
    """入力地域名を正規化（空白・都/府/県接尾辞・ローマ字対応）。"""
    s = re.sub(r"[\s\u3000]+", "", str(name)).strip()
    if not s:
        return "全国"
    # ローマ字（大小無視）→ 漢字
    key = s.lower()
    if key in ROMAJI_TO_JA:
        return ROMAJI_TO_JA[key]
    # 都/府/県/道 を除去して再試行（東京都→東京、北海道→北海道はROMAJI側で吸収済み）
    stripped = re.sub(r"(都|府|県)$", "", s)
    if stripped in ROMAJI:
        return stripped
    if stripped == "北海道":
        return "北海道"
    return s


def _norm_header(h: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(h)) if h is not None else ""


def parse_workbook(xlsx_bytes: bytes) -> dict[str, Any]:
    """s5.xlsxバイト列をパースして製品別週次レコードを返す。

    返り値:
      {
        "products": {product_key: [{"date": "YYYY-MM-DD", "national": float,
                                    "regions": {name: float|None}, "gas_tax": float|None}, ...]},
        "regions": [全地域名...],
        "prefectures": [47都道府県名...],
      }
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    out: dict[str, list[dict[str, Any]]] = {}
    regions: list[str] = []
    prefectures: list[str] = []

    for sheet, product in PRODUCT_SHEETS.items():
        if sheet not in wb.sheetnames:
            logger.warning(f"sheet missing: {sheet}")
            continue
        ws = wb[sheet]
        rows = ws.iter_rows(values_only=True)
        header = next(rows)
        cols: dict[int, str] = {}
        for i, h in enumerate(header):
            n = _norm_header(h)
            if i < 2 or not n or n == "None" or "税" in n:
                continue
            n = PREF_BUREAUS.get(n, n)  # 北海道局→北海道、沖縄局→沖縄
            if n == "全国":
                cols[i] = "全国"
            else:
                cols[i] = n
        if not regions:
            regions = [v for v in cols.values()]
            prefectures = [v for v in cols.values() if v not in BUREAUS and v != "全国"]
        tax_col = next((i for i, h in enumerate(header) if "うちガソリン税" in _norm_header(h)), None)

        records: list[dict[str, Any]] = []
        for row in rows:
            d = row[1]
            if isinstance(d, datetime):
                ds = d.date().isoformat()
            elif isinstance(d, str) and re.match(r"\d{4}-\d{2}-\d{2}", d):
                ds = d[:10]
            else:
                continue  # 末尾の注釈行など
            vals: dict[str, float | None] = {}
            for i, name in cols.items():
                v = row[i] if i < len(row) else None
                vals[name] = round(float(v), 2) if isinstance(v, (int, float)) and v else None
            gas_tax = None
            if tax_col is not None and tax_col < len(row) and isinstance(row[tax_col], (int, float)):
                gas_tax = round(float(row[tax_col]), 2)
            records.append({
                "date": ds,
                "national": vals.get("全国"),
                "regions": {k: v for k, v in vals.items() if k != "全国"},
                "gas_tax": gas_tax,
            })
        # 都道府県別は直近RETENTION_WEEKS週のみ保持、それより古い週は全国値だけ残す
        # （全国トレンドは1990年〜の全期間を参照できる）
        cutoff = records[-RETENTION_WEEKS]["date"] if len(records) > RETENTION_WEEKS else records[0]["date"]
        compact: list[dict[str, Any]] = []
        for r in records:
            if r["date"] >= cutoff:
                compact.append(r)
            else:
                compact.append({"date": r["date"], "national": r["national"],
                                "regions": {}, "gas_tax": r.get("gas_tax")})
        out[product] = compact
    wb.close()
    return {"products": out, "regions": regions, "prefectures": prefectures}


def _http_get(url: str, timeout: int = 90) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if data[:2] == b"PK":  # zip magic = xlsx
            return data
        logger.warning(f"not xlsx from {url}: first bytes {data[:20]!r}")
        return None
    except Exception as e:  # noqa: BLE001 — 403/404/timeout全部次の候補へ
        logger.info(f"GET {url} failed: {e}")
        return None


def fetch_latest(force: bool = False) -> dict[str, Any]:
    """キャッシュがあれば返し、なければ（またはTTL切れなら）XLSXを取得して更新。

    解決順: ワークキャッシュ → (TTL内) / 新XLSX取得 → 古いワークキャッシュ → シードキャッシュ
    戻り値は parse_workbook の出力 + {"source_file", "fetched_at", "latest_date"}
    """
    path = cache_path()
    if not force and os.path.exists(path):
        cached = _load_json(path)
        if cached and datetime.now().timestamp() - cached.get("fetched_at_epoch", 0) < CACHE_TTL_SECONDS:
            return cached

    if not _recent_failure():
        today = date.today()
        # 調査週は水曜、ファイル名は調査日基準で前後する。最大14日分を新順に試す。
        for delta in range(0, 15):
            d = today - timedelta(days=delta)
            ymd = d.strftime("%y%m%d")
            data = _http_get(BASE_URL.format(ymd=ymd))
            if data is None:
                continue
            parsed = parse_workbook(data)
            parsed["source_file"] = f"{ymd}s5.xlsx"
            parsed["fetched_at"] = datetime.now().isoformat(timespec="seconds")
            parsed["fetched_at_epoch"] = datetime.now().timestamp()
            latest = ""
            for recs in parsed["products"].values():
                if recs:
                    latest = max(latest, recs[-1]["date"])
            parsed["latest_date"] = latest
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(parsed, f, ensure_ascii=False)
            os.replace(tmp, path)
            logger.info(f"fuel data refreshed from {ymd}s5.xlsx latest={latest}")
            return parsed
        _mark_failure()
    else:
        logger.info("skipping METI fetch (in backoff window)")

    # 取得失敗/バックオフ中: ワークキャッシュ → シードキャッシュの順で耐える
    stale = _load_json(path) or _load_json(SEED_PATH)
    if stale:
        logger.warning("serving stale/seed fuel cache")
        return stale
    raise RuntimeError("METI fuel price XLSX could not be fetched and no cache exists")


# ──────────────────────────────────────────────
# クエリAPI（server.py から呼ばれる）
# ──────────────────────────────────────────────

def _product_key(product: str) -> str:
    p = str(product).strip().lower()
    aliases = {
        "premium": "premium", "high_octane": "premium", "ハイオク": "premium",
        "regular": "regular", "レギュラー": "regular", "gasoline": "regular",
        "ガソリン": "regular", "regular_gasoline": "regular",
        "diesel": "diesel", "軽油": "diesel",
        "kerosene": "kerosene_store", "kerosene_store": "kerosene_store",
        "kerosene_store_pickup": "kerosene_store", "灯油店頭": "kerosene_store",
        "kerosene_delivery": "kerosene_delivery", "灯油配達": "kerosene_delivery",
    }
    if p not in aliases:
        raise ValueError(f"unknown product '{product}'. Valid: premium, regular, diesel, kerosene_store, kerosene_delivery")
    return aliases[p]


def latest_price(region: str = "全国", product: str = "regular") -> dict[str, Any]:
    data = fetch_latest()
    pk = _product_key(product)
    recs = data["products"].get(pk) or []
    if not recs:
        raise RuntimeError(f"no data for product {pk}")
    reg = normalize_region(region)
    latest, prev = recs[-1], (recs[-2] if len(recs) > 1 else None)

    def value_of(rec: dict[str, Any]) -> float | None:
        if reg == "全国":
            return rec.get("national")
        return rec.get("regions", {}).get(reg)

    val = value_of(latest)
    if val is None:
        known = sorted(set(list(latest.get("regions", {}).keys()) + ["全国"]))
        raise ValueError(f"no value for region '{reg}' in {pk}. Known regions: {', '.join(known)}")
    prev_val = value_of(prev) if prev else None
    return {
        "region": reg,
        "region_romaji": ROMAJI.get(reg, reg),
        "product": pk,
        "product_label": PRODUCT_LABELS[pk],
        "unit": PRODUCT_UNITS[pk],
        "survey_date": latest["date"],
        "price": val,
        "previous_price": prev_val,
        "previous_date": prev["date"] if prev else None,
        "week_over_week": round(val - prev_val, 2) if (val is not None and prev_val is not None) else None,
        "gas_tax_component": latest.get("gas_tax") if pk in ("premium", "regular") else None,
        "source": "Agency for Natural Resources and Energy (METI), Government Standard Terms of Use 2.0",
    }


def price_history(region: str = "全国", product: str = "regular", weeks: int = 12) -> dict[str, Any]:
    data = fetch_latest()
    pk = _product_key(product)
    recs = data["products"].get(pk) or []
    reg = normalize_region(region)
    weeks = max(1, min(int(weeks), 260))
    series: list[dict[str, Any]] = []
    for rec in recs[-weeks:]:
        v = rec.get("national") if reg == "全国" else rec.get("regions", {}).get(reg)
        if v is not None:
            series.append({"date": rec["date"], "price": v})
    prices = [s["price"] for s in series]
    return {
        "region": reg,
        "product": pk,
        "unit": PRODUCT_UNITS[pk],
        "weeks_returned": len(series),
        "series": series,
        "min": min(prices) if prices else None,
        "max": max(prices) if prices else None,
        "avg": round(sum(prices) / len(prices), 2) if prices else None,
        "source": "METI Agency for Natural Resources and Energy, Government Standard Terms of Use 2.0",
    }


def cheapest_regions(product: str = "regular", limit: int = 10) -> dict[str, Any]:
    """47都道府県の最新週平均が安い順ランキング。"""
    data = fetch_latest()
    pk = _product_key(product)
    recs = data["products"].get(pk) or []
    if not recs:
        raise RuntimeError(f"no data for product {pk}")
    latest = recs[-1]
    limit = max(1, min(int(limit), 47))
    rows = [
        {"prefecture": name, "romaji": ROMAJI.get(name, name), "price": v}
        for name, v in latest["regions"].items()
        if name not in BUREAUS and name != "全国" and v is not None
    ]
    rows.sort(key=lambda r: r["price"])
    return {
        "product": pk,
        "unit": PRODUCT_UNITS[pk],
        "survey_date": latest["date"],
        "national_average": latest.get("national"),
        "cheapest": rows[:limit],
        "most_expensive": list(reversed(rows[-limit:])),
        "source": "METI Agency for Natural Resources and Energy, Government Standard Terms of Use 2.0",
    }


def national_trend(products: str = "premium,regular,diesel", weeks: int = 52) -> dict[str, Any]:
    """全国平均の複数製品トレンド（インフレ/マクロ指標用）。"""
    data = fetch_latest()
    weeks = max(1, min(int(weeks), 260))
    keys = [_product_key(p) for p in products.split(",") if p.strip()]
    out: dict[str, Any] = {"weeks": weeks, "series": {}, "latest": {}}
    for pk in keys:
        recs = (data["products"].get(pk) or [])[-weeks:]
        series = [{"date": r["date"], "price": r["national"]} for r in recs if r.get("national") is not None]
        out["series"][pk] = series
        if len(series) >= 2:
            first, last = series[0]["price"], series[-1]["price"]
            out["latest"][pk] = {
                "price": last,
                "survey_date": series[-1]["date"],
                "change_vs_window_start": round(last - first, 2),
                "pct_change": round((last - first) / first * 100, 2) if first else None,
            }
    out["unit_note"] = "gasoline/diesel: JPY/liter, kerosene: JPY/18L (tax-inclusive cash price)"
    out["source"] = "METI Agency for Natural Resources and Energy, Government Standard Terms of Use 2.0"
    return out


def list_regions() -> dict[str, Any]:
    data = fetch_latest()
    return {
        "prefectures": [
            {"name": p, "romaji": ROMAJI.get(p, p)} for p in data["prefectures"]
        ],
        "bureaus": [
            {"name": b, "romaji": ROMAJI.get(b, b)} for b in data["regions"] if b in BUREAUS
        ],
        "national": {"name": "全国", "romaji": "nationwide"},
        "products": {k: {"label": PRODUCT_LABELS[k], "unit": PRODUCT_UNITS[k]} for k in PRODUCT_SHEETS.values()},
        "latest_survey_date": data.get("latest_date"),
    }
