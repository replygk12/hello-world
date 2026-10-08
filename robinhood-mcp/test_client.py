"""Smoke test: log in with the credentials from .env and fetch a quote.

Run this once in a terminal before using the MCP server. It allows interactive
prompts, so if Robinhood asks for an SMS/email code or app approval you can
complete it here; the session is then pickled and reused by server.py.

    python test_client.py [SYMBOL]
"""

from __future__ import annotations

import json
import sys

import robinhood_client as rc


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

    result = rc.login(interactive=True)
    print("login:", json.dumps(result, indent=2))
    if not result["ok"]:
        return 1

    quote = rc.get_quote(symbol)
    if not quote["ok"]:
        print("quote:", json.dumps(quote, indent=2))
        return 1
    q = quote["data"]
    print(
        f"{q.get('symbol')}: last={q.get('last_trade_price')} "
        f"bid={q.get('bid_price')} ask={q.get('ask_price')} "
        f"prev_close={q.get('previous_close')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
