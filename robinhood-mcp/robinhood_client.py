"""Thin wrapper around ``robin_stocks.robinhood``.

Every public function returns a structured dict instead of raising:

    {"ok": True, "data": ...}
    {"ok": False, "error": {"type": "...", "message": "..."}}

robin_stocks prints progress messages to stdout and may call ``input()`` during
login. Both would corrupt an MCP stdio transport, so all library calls run with
stdout redirected to stderr, and non-interactive logins refuse to prompt.
"""

from __future__ import annotations

import builtins
import contextlib
import functools
import getpass
import os
import re
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyotp
import robin_stocks.robinhood as rh
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
ORDER_SIDES = {"buy", "sell"}
ORDER_TYPES = {"market", "limit"}
HISTORICAL_INTERVALS = {"5minute", "10minute", "hour", "day", "week"}
HISTORICAL_SPANS = {"day", "week", "month", "3month", "year", "5year"}
HISTORICAL_BOUNDS = {"regular", "extended", "trading"}

_login_lock = threading.Lock()
_logged_in = False


class RobinhoodError(Exception):
    """Base error for this module; ``type`` ends up in the error dict."""

    type = "robinhood_error"


class ValidationError(RobinhoodError):
    type = "validation_error"


class AuthError(RobinhoodError):
    type = "auth_error"


class APIError(RobinhoodError):
    type = "api_error"


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def _err(error_type: str, message: str) -> dict:
    return {"ok": False, "error": {"type": error_type, "message": message}}


@contextlib.contextmanager
def _quiet():
    """Send anything robin_stocks prints to stderr instead of stdout."""
    rh.set_output(sys.stderr)
    with contextlib.redirect_stdout(sys.stderr):
        yield


@contextlib.contextmanager
def _no_prompts():
    """Make interactive prompts fail fast instead of blocking on stdin."""

    def _refuse(*_args, **_kwargs):
        raise AuthError(
            "Robinhood requested interactive verification (SMS/email code or "
            "device approval). Run `python test_client.py` once in a terminal to "
            "complete it; the saved session will then be reused."
        )

    orig_input, orig_getpass = builtins.input, getpass.getpass
    builtins.input, getpass.getpass = _refuse, _refuse
    try:
        yield
    finally:
        builtins.input, getpass.getpass = orig_input, orig_getpass


def _handle_errors(fn: Callable[..., Any]) -> Callable[..., dict]:
    """Run ``fn`` quietly and convert its result/exception to a structured dict."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict:
        try:
            with _quiet():
                return _ok(fn(*args, **kwargs))
        except RobinhoodError as e:
            return _err(e.type, str(e))
        except Exception as e:  # noqa: BLE001 - robin_stocks raises a mix of requests/Key/Type errors
            return _err("unexpected_error", f"{type(e).__name__}: {e}")

    return wrapper


def _check_response(data: Any, what: str) -> Any:
    """robin_stocks often returns None or an error payload instead of raising."""
    if data is None:
        raise APIError(f"{what} failed: empty response from Robinhood")
    if isinstance(data, dict):
        for key in ("detail", "non_field_errors", "error"):
            if key in data and not data.get("id"):
                raise APIError(f"{what} failed: {data[key]}")
    return data


# ---------------------------------------------------------------- validation


def validate_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValidationError("symbol must be a non-empty string")
    sym = symbol.strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValidationError(f"invalid ticker symbol: {symbol!r}")
    return sym


def validate_order(
    symbol: Any, quantity: Any, side: Any, order_type: Any, limit_price: Any
) -> dict:
    sym = validate_symbol(symbol)

    if isinstance(quantity, bool):
        raise ValidationError("quantity must be a number")
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise ValidationError("quantity must be a number") from None
    if not qty > 0:
        raise ValidationError("quantity must be greater than 0")

    side_n = str(side).strip().lower()
    if side_n not in ORDER_SIDES:
        raise ValidationError(f"side must be one of {sorted(ORDER_SIDES)}")

    type_n = str(order_type).strip().lower()
    if type_n not in ORDER_TYPES:
        raise ValidationError(f"order_type must be one of {sorted(ORDER_TYPES)}")

    price = None
    if type_n == "limit":
        if limit_price is None:
            raise ValidationError("limit_price is required for limit orders")
        try:
            price = float(limit_price)
        except (TypeError, ValueError):
            raise ValidationError("limit_price must be a number") from None
        if not price > 0:
            raise ValidationError("limit_price must be greater than 0")
        if not qty.is_integer():
            raise ValidationError("limit orders require a whole-share quantity")
    elif limit_price is not None:
        raise ValidationError("limit_price is only valid for limit orders")

    return {
        "symbol": sym,
        "quantity": int(qty) if qty.is_integer() else qty,
        "side": side_n,
        "order_type": type_n,
        "limit_price": price,
    }


def validate_order_id(order_id: Any) -> str:
    try:
        return str(uuid.UUID(str(order_id).strip()))
    except (ValueError, AttributeError):
        raise ValidationError(f"invalid order_id: {order_id!r}") from None


# --------------------------------------------------------------------- auth


def _mfa_code() -> str | None:
    secret = os.getenv("RH_TOTP_SECRET", "").strip()
    if secret:
        try:
            return pyotp.TOTP(secret.replace(" ", "")).now()
        except Exception as e:  # noqa: BLE001
            raise AuthError(
                f"RH_TOTP_SECRET is not a valid base32 secret: {e}"
            ) from None
    return os.getenv("RH_MFA_CODE", "").strip() or None


def _login(interactive: bool = False, force: bool = False) -> dict:
    global _logged_in
    with _login_lock:
        if _logged_in and not force:
            return {"detail": "already logged in"}

        username = os.getenv("RH_USERNAME", "").strip()
        password = os.getenv("RH_PASSWORD", "")
        if not username or not password:
            raise AuthError(
                "RH_USERNAME and RH_PASSWORD must be set (see .env.example)"
            )

        try:
            expires = int(os.getenv("RH_SESSION_EXPIRES", "86400"))
        except ValueError:
            raise AuthError(
                "RH_SESSION_EXPIRES must be an integer number of seconds"
            ) from None

        pickle_dir = os.path.expanduser(
            os.getenv("RH_PICKLE_PATH", "")
        ) or os.path.join(os.path.expanduser("~"), ".tokens")
        pickle_name = os.getenv("RH_PICKLE_NAME", "")
        guard = contextlib.nullcontext() if interactive else _no_prompts()
        with guard:
            result = rh.login(
                username=username,
                password=password,
                expiresIn=expires,
                store_session=True,
                mfa_code=_mfa_code(),
                pickle_path=pickle_dir,
                pickle_name=pickle_name,
            )

        if not result or "access_token" not in result:
            # robin_stocks leaves an empty pickle behind after a failed login.
            stale = Path(pickle_dir) / f"robinhood{pickle_name}.pickle"
            if stale.is_file() and stale.stat().st_size == 0:
                stale.unlink()
            detail = (result or {}).get("detail") if isinstance(result, dict) else None
            raise AuthError(
                "Robinhood login failed"
                + (f": {detail}" if detail else "")
                + ". Check credentials and MFA settings (RH_TOTP_SECRET / RH_MFA_CODE)."
            )

        _logged_in = True
        # Never hand tokens back to callers.
        return {"detail": result.get("detail", "logged in")}


def _ensure_login() -> None:
    if not _logged_in:
        _login(interactive=False)


@_handle_errors
def login(interactive: bool = False, force: bool = False) -> dict:
    """Log in (or reuse the pickled session). ``interactive`` allows prompts."""
    return _login(interactive=interactive, force=force)


@_handle_errors
def logout() -> dict:
    global _logged_in
    with _login_lock:
        if _logged_in:
            rh.logout()
        _logged_in = False
    return {"detail": "logged out"}


# ------------------------------------------------------------------ account


@_handle_errors
def get_account_info() -> dict:
    _ensure_login()
    account = _check_response(rh.load_account_profile(), "load account profile")
    user = rh.load_user_profile() or {}
    return {
        "account": account,
        "user": {
            k: user.get(k) for k in ("username", "first_name", "last_name", "email")
        },
    }


@_handle_errors
def get_portfolio() -> dict:
    _ensure_login()
    return _check_response(rh.load_portfolio_profile(), "load portfolio profile")


@_handle_errors
def get_positions() -> dict:
    """Open stock positions keyed by symbol (price, quantity, equity, P/L...)."""
    _ensure_login()
    holdings = rh.build_holdings()
    if holdings is None:
        raise APIError("load positions failed: empty response from Robinhood")
    return holdings


# -------------------------------------------------------------- market data


@_handle_errors
def get_quote(symbol: str) -> dict:
    sym = validate_symbol(symbol)
    _ensure_login()
    quotes = rh.get_quotes(sym)
    if not quotes or quotes[0] is None:
        raise APIError(f"no quote found for {sym}")
    return quotes[0]


@_handle_errors
def get_historicals(
    symbol: str, interval: str = "day", span: str = "month", bounds: str = "regular"
) -> list:
    sym = validate_symbol(symbol)
    if interval not in HISTORICAL_INTERVALS:
        raise ValidationError(f"interval must be one of {sorted(HISTORICAL_INTERVALS)}")
    if span not in HISTORICAL_SPANS:
        raise ValidationError(f"span must be one of {sorted(HISTORICAL_SPANS)}")
    if bounds not in HISTORICAL_BOUNDS:
        raise ValidationError(f"bounds must be one of {sorted(HISTORICAL_BOUNDS)}")
    if bounds != "regular" and span != "day":
        raise ValidationError("extended/trading bounds require span='day'")
    _ensure_login()
    data = rh.get_stock_historicals(sym, interval=interval, span=span, bounds=bounds)
    if not data or data == [None]:
        raise APIError(f"no historical data found for {sym}")
    return [row for row in data if row is not None]


# ------------------------------------------------------------------- orders


def _add_symbol(order: dict) -> dict:
    url = order.get("instrument")
    if url and "symbol" not in order:
        order["symbol"] = rh.get_symbol_by_url(url)
    return order


@_handle_errors
def place_order(
    symbol: str,
    quantity: float,
    side: str,
    order_type: str = "market",
    limit_price: float | None = None,
    dry_run: bool = True,
) -> dict:
    """Validate and (only when ``dry_run`` is False) submit a stock order."""
    params = validate_order(symbol, quantity, side, order_type, limit_price)

    if dry_run is not False:
        preview: dict[str, Any] = {"dry_run": True, "submitted": False, "order": params}
        try:
            _ensure_login()
            prices = rh.get_latest_price(params["symbol"])
            last = float(prices[0]) if prices and prices[0] else None
        except Exception as e:  # noqa: BLE001 - preview is best-effort
            last = None
            preview["quote_error"] = str(e)
        if last is not None:
            ref = params["limit_price"] or last
            preview["last_price"] = last
            preview["estimated_value"] = round(ref * float(params["quantity"]), 2)
        preview["note"] = "Dry run only. Call again with dry_run=False to submit."
        return preview

    _ensure_login()
    result = rh.order(
        params["symbol"],
        params["quantity"],
        params["side"],
        limitPrice=params["limit_price"],
        timeInForce="gfd" if params["order_type"] == "market" else "gtc",
    )
    result = _check_response(result, "place order")
    if not result.get("id"):
        raise APIError(f"place order failed: {result}")
    return {"dry_run": False, "submitted": True, "order": _add_symbol(result)}


@_handle_errors
def get_orders(open_only: bool = True) -> list:
    _ensure_login()
    orders = rh.get_all_open_stock_orders() if open_only else rh.get_all_stock_orders()
    if orders is None:
        raise APIError("load orders failed: empty response from Robinhood")
    return [_add_symbol(o) for o in orders]


@_handle_errors
def cancel_order(order_id: str) -> dict:
    oid = validate_order_id(order_id)
    _ensure_login()
    result = rh.cancel_stock_order(oid)
    _check_response(result, f"cancel order {oid}")
    return {"order_id": oid, "cancel_requested": True}
