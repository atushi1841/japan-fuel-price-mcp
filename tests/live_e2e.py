"""ライブE2E: 実際のMETI XLSX取得→パース→ツール応答を確認（ネットワーク要）。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["FUEL_DATA_DIR"] = tempfile.mkdtemp(prefix="fuel-live-")

from src import fuel_price  # noqa: E402

data = fuel_price.fetch_latest(force=True)
print("source_file:", data.get("source_file"))
print("latest_date:", data.get("latest_date"))
r = fuel_price.latest_price("全国", "regular")
print("nationwide regular:", r["price"], r["survey_date"], "WoW", r["week_over_week"])
c = fuel_price.cheapest_regions("regular", 3)
print("cheapest3:", [(x["prefecture"], x["price"]) for x in c["cheapest"]])
assert data.get("latest_date", "") >= "2026-09-01", "stale data"
print("LIVE E2E OK")
