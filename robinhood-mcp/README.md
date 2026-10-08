# Robinhood MCP server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes a Robinhood
brokerage account to MCP clients (Claude Desktop, etc.). Built on the official Python SDK's
`FastMCP` and the community `robin-stocks` library, served over **stdio**.

> **Disclaimer.** Robinhood does **not** offer an official public API for stock trading.
> This project uses [`robin-stocks`](https://github.com/jmfernandes/robin_stocks), an
> **unofficial, community-maintained** library that talks to Robinhood's private endpoints.
> Those endpoints can change or break at any time, and automated access may conflict with
> Robinhood's terms of service. Use at your own risk; nothing here is financial advice.

## Tools

| Tool | Kind | Description |
| --- | --- | --- |
| `get_account_info()` | read-only | Account profile (buying power, cash, type) and basic user info |
| `get_portfolio()` | read-only | Equity, market value, withdrawable amount |
| `get_positions()` | read-only | Open positions keyed by symbol (qty, avg cost, price, equity, P/L) |
| `get_quote(symbol)` | market data | Latest quote (bid/ask, last trade, previous close) |
| `get_historicals(symbol, interval, span)` | market data | OHLCV candles. `interval`: `5minute`/`10minute`/`hour`/`day`/`week`; `span`: `day`/`week`/`month`/`3month`/`year`/`5year` |
| `place_order(symbol, quantity, side, order_type, limit_price=None, dry_run=True)` | **trading** | `side`: `buy`/`sell`; `order_type`: `market`/`limit`. Dry run by default |
| `get_orders(open_only=True)` | read-only | Open orders, or full history with `open_only=False` |
| `cancel_order(order_id)` | **trading** | Cancel an open order by UUID |

Every tool returns a structured result instead of raising:

```json
{"ok": true, "data": {...}}
{"ok": false, "error": {"type": "validation_error", "message": "limit_price is required for limit orders"}}
```

Error types: `validation_error`, `auth_error`, `api_error`, `unexpected_error`.

## Setup

Requires Python 3.10+.

```bash
cd robinhood-mcp
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then edit .env
```

`requirements.txt` pins `mcp<2`: in `mcp` 2.x, `FastMCP` was renamed to `MCPServer`.

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `RH_USERNAME` | yes | Robinhood login email/username |
| `RH_PASSWORD` | yes | Robinhood password |
| `RH_TOTP_SECRET` | no | Base32 authenticator-app secret; a fresh MFA code is generated with `pyotp` on each login |
| `RH_MFA_CODE` | no | One-off 6-digit MFA code (expires in ~30s; only useful for a single manual login) |
| `RH_PICKLE_PATH` | no | Directory for the saved session (default `~/.tokens`) |
| `RH_PICKLE_NAME` | no | Suffix for the session file name (`robinhood<NAME>.pickle`), for multiple accounts |
| `RH_SESSION_EXPIRES` | no | Requested session lifetime in seconds (default `86400`) |

Variables are read from the process environment, and from `.env` next to `robinhood_client.py`.

### Smoke test

```bash
python test_client.py          # logs in and prints an AAPL quote
python test_client.py TSLA
```

Run this **once in a terminal before registering the server** with a client (see MFA notes).

## MFA and session persistence

- Login uses `robin_stocks`' pickle-based session storage. After the first successful login
  the token is saved to `~/.tokens/robinhood<RH_PICKLE_NAME>.pickle` (or `RH_PICKLE_PATH`) and
  reused on later starts, so you are not asked for MFA every time. The file contains a live
  access token: keep it private and never commit it.
- **Authenticator app (recommended):** in Robinhood, enable two-factor authentication with an
  authenticator app and save the setup key (the base32 secret behind the QR code) as
  `RH_TOTP_SECRET`. Codes are generated automatically.
- **SMS / email / device approval:** Robinhood may start a verification challenge that needs
  a code typed in or approval in the Robinhood app. `test_client.py` allows these interactive
  prompts. The MCP server does **not** (stdin is the MCP channel), so when a challenge is
  required it returns an `auth_error` asking you to run `test_client.py` first. Once the
  session is pickled, the server reuses it.
- Login is lazy: the server logs in on the first tool call that needs it.
- All `robin_stocks` console output is redirected to stderr so it cannot corrupt the stdio
  protocol stream.

## Registering with an MCP client

Claude Desktop (`claude_desktop_config.json`; macOS: `~/Library/Application Support/Claude/`,
Windows: `%APPDATA%\Claude\`). Use absolute paths, and point `command` at the virtualenv's
Python so the dependencies are found:

```json
{
  "mcpServers": {
    "robinhood": {
      "command": "/absolute/path/to/hello-world/robinhood-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/hello-world/robinhood-mcp/server.py"],
      "env": {
        "RH_USERNAME": "you@example.com",
        "RH_PASSWORD": "your-password",
        "RH_TOTP_SECRET": "YOURBASE32SECRET"
      }
    }
  }
}
```

The `env` block is optional if you keep credentials in `.env`. Restart Claude Desktop after
editing the config. To debug the server directly you can use the MCP Inspector:
`npx @modelcontextprotocol/inspector .venv/bin/python server.py`.

## Safety notes

- `place_order` defaults to **`dry_run=True`**: the order is validated and previewed (latest
  price, estimated value) and **nothing is sent to Robinhood**. An order is only submitted
  when the caller passes `dry_run=false` explicitly.
- `cancel_order` acts immediately. It only accepts a valid order UUID.
- Input validation runs before any API call: ticker format (`^[A-Z][A-Z0-9.-]{0,9}$`, upper-cased),
  `quantity > 0`, `side` in `buy`/`sell`, `order_type` in `market`/`limit`, `limit_price > 0`
  and required for limit orders only, whole-share quantities for limit orders.
- Market orders are sent good-for-day (`gfd`); limit orders good-till-cancelled (`gtc`).
- Configure your MCP client to require manual approval for tool calls (Claude Desktop asks by
  default) and review every `place_order` call with `dry_run=false` before approving it.
- Consider a dedicated or low-balance account while trying this out.
