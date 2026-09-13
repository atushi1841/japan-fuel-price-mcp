"""
stdio エントリポイント — MCPBバンドル（ローカル実行）用。

Apify Standby（HTTP）ではなく、Claude Desktop / Cursor / Smithery の
ローカル配布（MCPB）として起動するときの入り口。

- transport は FastMCP 既定の stdio（stdout は JSON-RPC 専用、ログは stderr）
- Apify ランタイムが無い環境では src/apify_shim.py の no-op Actor が使われる
- データは METI 公式XLSXを遅延取得（TTL 6h）。失敗時は同梱シードキャッシュにフォールバック
"""

from __future__ import annotations

import logging
import os
import sys

# `uv run --directory <bundle> src/stdio_main.py` で起動されると sys.path[0] が
# <bundle>/src になり `import src.*` が解決できないため、バンドル直下を明示的に追加する。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.server import get_server  # noqa: E402


def main() -> None:
    # stdio トランスポートでは stdout が JSON-RPC 専用。ログは stderr に出す。
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    server = get_server()
    server.run()  # FastMCP 既定 transport = stdio


if __name__ == "__main__":
    main()
