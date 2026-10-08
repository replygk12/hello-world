"""Robinhood MCP server (FastMCP, stdio transport).

All tools return ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"type": ..., "message": ...}}``.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

import robinhood_client as rc

mcp = FastMCP("robinhood")


@mcp.tool()
def get_account_info() -> dict:
    """Read-only. Robinhood account profile (buying power, cash, account type) and basic user info."""
    return rc.get_account_info()


@mcp.tool()
def get_portfolio() -> dict:
    """Read-only. Portfolio summary: equity, market value, extended-hours equity, withdrawable amount."""
    return rc.get_portfolio()


@mcp.tool()
def get_positions() -> dict:
    """Read-only. Open stock positions keyed by symbol with quantity, average cost, price, equity and P/L."""
    return rc.get_positions()


@mcp.tool()
def get_quote(symbol: str) -> dict:
    """Latest quote for a stock ticker (e.g. "AAPL"): bid/ask, last trade price, previous close."""
    return rc.get_quote(symbol)


@mcp.tool()
def get_historicals(
    symbol: str,
    interval: Literal["5minute", "10minute", "hour", "day", "week"] = "day",
    span: Literal["day", "week", "month", "3month", "year", "5year"] = "month",
) -> dict:
    """Historical OHLCV candles for a ticker at the given interval over the given span."""
    return rc.get_historicals(symbol, interval=interval, span=span)


@mcp.tool()
def place_order(
    symbol: str,
    quantity: float,
    side: Literal["buy", "sell"],
    order_type: Literal["market", "limit"],
    limit_price: float | None = None,
    dry_run: bool = True,
) -> dict:
    """Place a stock order. SAFE BY DEFAULT: with dry_run=True (the default) the order is only
    validated and previewed (estimated value) and NOTHING is sent to Robinhood. A real order is
    submitted only when dry_run is explicitly False. limit_price is required for limit orders.
    Market orders are good-for-day; limit orders are good-till-cancelled."""
    return rc.place_order(
        symbol, quantity, side, order_type, limit_price, dry_run=dry_run
    )


@mcp.tool()
def get_orders(open_only: bool = True) -> dict:
    """List stock orders. By default only open (unfilled/pending) orders; set open_only=False for full history."""
    return rc.get_orders(open_only=open_only)


@mcp.tool()
def cancel_order(order_id: str) -> dict:
    """Cancel an open stock order by its Robinhood order ID (UUID, see get_orders)."""
    return rc.cancel_order(order_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
