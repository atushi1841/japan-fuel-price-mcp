"""
data/fuel_prices.json（シードキャッシュ）生成スクリプト。

使い方:
  python3 scripts/refresh_seed.py [path/to/s5.xlsx]

  - 引数なし: METIから最新YYMMDDs5.xlsxを取得して生成
  - 引数あり: ローカルXLSXから生成（レート制限時に手元DL分を使う）

生成物を git commit して push → Apify再ビルドでシードが更新される。
週1回（木曜公開後）に回すのが理想。kensho側のcronから実行可。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import fuel_price  # noqa: E402


def main() -> None:
    if len(sys.argv) > 1:
        xlsx = Path(sys.argv[1])
        data = fuel_price.parse_workbook(xlsx.read_bytes())
        data["source_file"] = xlsx.name
    else:
        data = fuel_price.fetch_latest(force=True)

    data["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    data["fetched_at_epoch"] = 0  # シードは常にTTL切れ扱い → 初回実呼び出しでライブ更新を試みる
    if not data.get("latest_date"):
        data["latest_date"] = max(
            (recs[-1]["date"] for recs in data["products"].values() if recs), default=None
        )
    out = ROOT / "data" / "fuel_prices.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    size_kb = out.stat().st_size // 1024
    print(f"wrote {out} ({size_kb} KB) latest_date={data.get('latest_date')} source={data.get('source_file')}")


if __name__ == "__main__":
    main()
