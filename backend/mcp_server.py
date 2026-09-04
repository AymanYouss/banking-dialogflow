"""
Banking MCP server.

Exposes the "real" banking actions as MCP tools over stdio. This process is the
single source of truth for the demo bank's state (accounts + transactions). The
FastAPI backend spawns it as an MCP client and calls these tools; any other
MCP-capable client (e.g. Claude Desktop) could call the exact same tools.

Tools:
  - get_accounts()                          -> list of accounts with balances
  - get_transactions(account_id?, limit?)   -> recent transactions
  - transfer_funds(from_account, to_account, amount)
  - pay_bill(account_id, payee, amount)

Run standalone (for manual testing):  python backend/mcp_server.py
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("banking")

# ---------------------------------------------------------------------------
# In-memory demo state (resets when the process restarts — it's a prototype)
# ---------------------------------------------------------------------------

ACCOUNTS: dict[str, dict[str, Any]] = {
    "checking": {"id": "checking", "name": "Everyday Checking", "type": "checking", "balance": 4210.55, "currency": "USD"},
    "savings": {"id": "savings", "name": "Rainy Day Savings", "type": "savings", "balance": 12850.00, "currency": "USD"},
    "credit": {"id": "credit", "name": "Platinum Credit Card", "type": "credit", "balance": -742.18, "currency": "USD"},
}

TRANSACTIONS: list[dict[str, Any]] = [
    {"id": "t1", "account_id": "checking", "date": "2026-09-01", "description": "Whole Foods Market", "amount": -86.42},
    {"id": "t2", "account_id": "checking", "date": "2026-09-01", "description": "Salary — Acme Corp", "amount": 3200.00},
    {"id": "t3", "account_id": "checking", "date": "2026-08-30", "description": "Uber", "amount": -18.90},
    {"id": "t4", "account_id": "savings", "date": "2026-08-28", "description": "Interest", "amount": 12.15},
    {"id": "t5", "account_id": "credit", "date": "2026-08-27", "description": "Amazon", "amount": -134.99},
]


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _resolve_account(token: str | None) -> dict[str, Any] | None:
    """Match an account by id, type, or a fuzzy name substring."""
    if not token:
        return None
    token = token.strip().lower()
    if token in ACCOUNTS:
        return ACCOUNTS[token]
    for acc in ACCOUNTS.values():
        if token == acc["type"] or token in acc["name"].lower():
            return acc
    return None


def _fmt(amount: float, currency: str = "USD") -> str:
    return f"${amount:,.2f}" if currency == "USD" else f"{amount:,.2f} {currency}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_accounts() -> dict[str, Any]:
    """Return all of the customer's accounts and their current balances."""
    return {"accounts": list(ACCOUNTS.values())}


@mcp.tool()
def get_transactions(account_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Return recent transactions, optionally filtered to a single account.

    account_id: 'checking', 'savings', 'credit', or a name fragment. Omit for all.
    limit: max number of transactions to return (most recent first).
    """
    acc = _resolve_account(account_id) if account_id else None
    rows = TRANSACTIONS
    if acc:
        rows = [t for t in rows if t["account_id"] == acc["id"]]
    rows = list(reversed(rows))[: max(1, min(limit, 50))]
    return {"account_id": acc["id"] if acc else None, "transactions": rows}


@mcp.tool()
def transfer_funds(from_account: str, to_account: str, amount: float) -> dict[str, Any]:
    """Move money between two of the customer's own accounts.

    from_account / to_account: 'checking', 'savings', 'credit', or a name fragment.
    amount: positive number in USD.
    """
    src = _resolve_account(from_account)
    dst = _resolve_account(to_account)
    if src is None:
        return {"ok": False, "error": f"Unknown source account: {from_account!r}"}
    if dst is None:
        return {"ok": False, "error": f"Unknown destination account: {to_account!r}"}
    if src["id"] == dst["id"]:
        return {"ok": False, "error": "Source and destination accounts are the same."}
    if amount <= 0:
        return {"ok": False, "error": "Amount must be positive."}
    if src["type"] != "credit" and src["balance"] < amount:
        return {"ok": False, "error": f"Insufficient funds in {src['name']} ({_fmt(src['balance'])})."}

    src["balance"] = round(src["balance"] - amount, 2)
    dst["balance"] = round(dst["balance"] + amount, 2)
    date = _now_date()
    TRANSACTIONS.append({"id": f"t_{uuid.uuid4().hex[:8]}", "account_id": src["id"], "date": date,
                         "description": f"Transfer to {dst['name']}", "amount": -amount})
    TRANSACTIONS.append({"id": f"t_{uuid.uuid4().hex[:8]}", "account_id": dst["id"], "date": date,
                         "description": f"Transfer from {src['name']}", "amount": amount})
    return {
        "ok": True,
        "message": f"Transferred {_fmt(amount)} from {src['name']} to {dst['name']}.",
        "from": {"id": src["id"], "balance": src["balance"]},
        "to": {"id": dst["id"], "balance": dst["balance"]},
    }


@mcp.tool()
def pay_bill(account_id: str, payee: str, amount: float) -> dict[str, Any]:
    """Pay a bill to a payee from the given account.

    account_id: which account to pay from ('checking', 'savings', or name fragment).
    payee: name of the biller (e.g. 'Electric Company').
    amount: positive number in USD.
    """
    acc = _resolve_account(account_id)
    if acc is None:
        return {"ok": False, "error": f"Unknown account: {account_id!r}"}
    if amount <= 0:
        return {"ok": False, "error": "Amount must be positive."}
    if acc["type"] != "credit" and acc["balance"] < amount:
        return {"ok": False, "error": f"Insufficient funds in {acc['name']} ({_fmt(acc['balance'])})."}

    acc["balance"] = round(acc["balance"] - amount, 2)
    TRANSACTIONS.append({"id": f"t_{uuid.uuid4().hex[:8]}", "account_id": acc["id"], "date": _now_date(),
                         "description": f"Bill payment — {payee}", "amount": -amount})
    return {
        "ok": True,
        "message": f"Paid {_fmt(amount)} to {payee} from {acc['name']}.",
        "account": {"id": acc["id"], "balance": acc["balance"]},
    }


if __name__ == "__main__":
    mcp.run()  # stdio transport
