#!/usr/bin/env python3
"""Japan Fuel Price API の RapidAPI メトリクスを取得して1レポートを出力する。

用途: Hermes cron（no_agent）から呼び、stdout をそのまま Telegram に配信する。
成功指標「2週間で外部30+ calls/day」の進捗を毎日1行で追うため。

- 認証: /mnt/d/Project2/goo-net-car-scraper/rapidapi_auth.json（cookies + csrf_token）
  cookie はブラウザ由来なので期限切れしうる。切れたら明確な警告行を出して exit 0
  （cron を失敗扱いにしない＝通知は届く）。
- 出力: 累計リクエスト数 / 前回スナップショットとの差分と日平均 / 外部購読数 / 状態。
  スナップショットは data/rapidapi_fuel_metrics.json（追記・最大400件）。

使い方:
    python3 scripts/rapidapi_metrics.py [--auth PATH] [--api-id ID] [--snapshot PATH]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

DEFAULT_AUTH = "/mnt/d/Project2/goo-net-car-scraper/rapidapi_auth.json"
DEFAULT_API_ID = "api_dbf7c702-51b3-436e-9e70-21c6c6c358d8"  # Japan Fuel Price API
DEFAULT_SNAPSHOT = "/mnt/d/Project2/kensho/data/rapidapi_fuel_metrics.json"

GRAPHQL_URL = "https://rapidapi.com/gateway/graphql"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
GOAL_PER_DAY = 30.0
MILESTONE = dt.date(2026, 9, 27)  # 中間判定日（カード t_742cfd52 の成功指標）


def fetch_metrics(auth: dict, api_id: str) -> dict:
    query = (
        'query { api(id: "%s") { id name requestsCount subscriptionsCount '
        "status visibility pricing } }" % api_id
    )
    cmd = [
        "curl", "-s", "-m", "60", "-X", "POST", GRAPHQL_URL,
        "-H", "content-type: application/json",
        "-H", f"csrf-token: {auth['csrf_token']}",
        "-H", f"x-entity-id: {auth['entity_id']}",
        "-H", "rapid-client: provider-dashboard-service",
        "-H", "origin: https://rapidapi.com",
        "-H", "referer: https://rapidapi.com/_studio/",
        "-H", f"user-agent: {UA}",
        "-b", auth["cookies"],
        "--data-binary", json.dumps({"query": query, "variables": {}}),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
    payload = json.loads(out)
    if payload.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in payload["errors"])[:200])
    api = (payload.get("data") or {}).get("api")
    if not api:
        raise RuntimeError("api ノードが空（API ID か cookie を確認）")
    return api


def load_snapshot(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_snapshot(path: str, history: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(history[-400:], fh, ensure_ascii=False, indent=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", default=DEFAULT_AUTH)
    ap.add_argument("--api-id", default=DEFAULT_API_ID)
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    args = ap.parse_args()

    now = dt.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")

    try:
        api = fetch_metrics(json.load(open(args.auth, encoding="utf-8")), args.api_id)
    except Exception as exc:  # noqa: BLE001 — cron を落とさず通知だけ出す
        print(f"⚠️ Japan Fuel Price API メトリクス取得失敗 ({stamp} JST)")
        print(f"   理由: {exc}")
        print("   → RapidAPI の cookie 期限切れなら rapidapi_auth.json を更新（F12 の Copy as cURL）")
        return 0

    history = load_snapshot(args.snapshot)
    total = int(api.get("requestsCount") or 0)
    subs = int(api.get("subscriptionsCount") or 0)

    prev = history[-1] if history else None
    if prev:
        delta = total - int(prev.get("requests", 0))
        prev_dt = dt.datetime.fromisoformat(prev["at"])
        days = max((now - prev_dt).total_seconds() / 86400.0, 1e-6)
        per_day = delta / days
        delta_txt = f"前回比 +{delta} / {days:.1f}日 → {per_day:.1f} req/day"
    else:
        delta = total
        per_day = 0.0
        delta_txt = "初回計測（前回なし）"

    history.append({"at": now.isoformat(timespec="seconds"), "requests": total, "subscriptions": subs})
    try:
        save_snapshot(args.snapshot, history)
    except OSError as exc:
        print(f"（スナップショット保存失敗: {exc}）")

    remain_days = (MILESTONE - now.date()).days
    status = "達成" if per_day >= GOAL_PER_DAY else f"あと{GOAL_PER_DAY - per_day:.1f}"
    print(f"📊 Japan Fuel Price API メトリクス ({stamp} JST)")
    print(f"   累計リクエスト: {total}  ({delta_txt})")
    print(f"   外部購読数: {subs} / 状態: {api.get('visibility')}-{api.get('pricing')} ({api.get('status')})")
    print(f"   目標 {GOAL_PER_DAY:.0f} req/day → {status}（9/27まで残り{max(remain_days, 0)}日）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
