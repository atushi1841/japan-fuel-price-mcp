"""
MCPサーバーエントリポイント — Apify Standby モード対応。

1. Actor.init() を呼び出してApifyプラットフォームに登録
2. uvicorn HTTPサーバーを起動（Apifyが期待するポート）
3. FastMCPアプリ（server.py）をHTTPサーバーに配線
4. SIGINT でgraceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import uvicorn

logging.basicConfig(level=logging.INFO)

if os.environ.get("APIFY_CONTAINER_PORT"):
    from apify import Actor
else:
    from src.apify_shim import Actor

from src.server import get_server


async def main() -> None:
    await Actor.init()

    port = int(os.environ.get("APIFY_CONTAINER_PORT") or os.environ.get("PORT") or "3000")

    server = get_server()
    app = server.http_app(transport="streamable-http")

    try:
        Actor.log.info(f"MCPサーバーを起動します (port {port})")
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        uvicorn_server = uvicorn.Server(config)
        await uvicorn_server.serve()
    except asyncio.CancelledError:
        Actor.log.info("MCPサーバーを停止します")
    finally:
        await Actor.exit()


if __name__ == "__main__":
    asyncio.run(main())
