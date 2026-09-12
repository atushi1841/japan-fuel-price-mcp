# Japan Fuel Price MCP — METI Weekly Gasoline / Diesel / Kerosene

MCPサーバー: 日本のガソリン・軽油・灯油の週次小売価格を、資源エネルギー庁（METI）公式統計XLSXからAIエージェントに提供します。

- データソース: [資源エネルギー庁 給油所小売価格調査](https://www.enecho.meti.go.jp/statistics/petroleum_and_lpgas/pl007/) 公式XLSX（毎週更新、1990年〜の全国・都道府県別平均現金価格）
- 利用規約: 政府標準利用規約2.0（出典明示で商用利用可）
- 単位: ガソリン・軽油 = 円/L、灯油 = 円/18L（消費税込み）

## Tools

| Tool | 説明 |
|------|------|
| `get_latest_fuel_price(prefecture, product)` | 指定都道府県（または全国）の最新週平均価格 + 前週比 + ガソリン税内訳 |
| `get_fuel_price_history(prefecture, product, weeks)` | 週次履歴（都道府県は直近260週、全国は1990年〜）+ min/max/avg |
| `cheapest_prefectures(product, limit)` | 47都道府県 最安〜最高値ランキング |
| `national_fuel_trend(products, weeks)` | 全国平均の複数製品トレンド（インフレ/マクロ指標用） |
| `list_fuel_regions()` | 照会可能な地域（漢字+ローマ字）・製品・単位のリスト |

`prefecture` は漢字・ローマ字・「都/府/県」付きのいずれでも可（東京 / tokyo / 東京都）。北海道・沖縄はMETI調査の局単位データを都道府県として提供。

## 動作

1. 初回呼び出しで最新 `YYMMDDs5.xlsx` を取得（ブラウザUA必須）、openpyxlでパースしJSONキャッシュ化
2. 以後6時間はキャッシュから即答、TTL切れで新週ファイルをチェック
3. METIのレート制限/障害時はビルド時同梱のシードキャッシュ（`data/fuel_prices.json`）にフォールバック — ツールが500になることはない

## MCP接続

Apify Standby モード（streamable HTTP）:

```
https://api.apify.com/v2/acts/<ACTOR_ID>/mcp
Authorization: Bearer <APIFY_TOKEN>
```

## ローカル開発

```bash
pip install -r requirements.txt
python3 -m pytest tests/ -q          # 14 tests, network-stubbed, seed cache
PORT=3999 python3 -m src.main &      # shim経由でApify SDK不要
python3 tests/http_smoke.py http://localhost:3999/mcp
```

シード更新（週1回、木曜の公開後）:

```bash
python3 scripts/refresh_seed.py          # METIから取得して data/fuel_prices.json 再生成
python3 scripts/refresh_seed.py path/to/YYMMDDs5.xlsx   # 手元DL分から生成（レート制限回避）
git add data/fuel_prices.json && git commit -m "data: seed refresh" && git push
```

## Deploy

GitHub連携 Apify Actor（`usesStandbyMode`, `webServerMcpPath=/mcp`）。push後にビルドAPIを叩く:

```bash
curl -X POST "https://api.apify.com/v2/acts/<ACTOR_ID>/builds?version=0.1&tag=latest&token=$APIFY_TOKEN"
```

## 出典表記

> Source: Agency for Natural Resources and Energy (METI), Petroleum Products Price Survey. Government Standard Terms of Use 2.0.
