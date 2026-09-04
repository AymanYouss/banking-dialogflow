"""
FastAPI backend for the Dialogflow CX banking prototype.

Responsibilities:
  1. Serve the single-page website (dashboard + embedded CX chat widget).
  2. Expose the Dialogflow CX *webhook* endpoint. CX calls this during a
     conversation; we translate the request into MCP tool calls.
  3. Push live state changes to the browser over Server-Sent Events (SSE) so the
     dashboard updates the instant an action runs.

Run:  uvicorn backend.app:app --reload --port 8080   (from the repo root)
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .mcp_client import BankingMCP

load_dotenv()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class Broadcaster:
    """Fan-out of state updates to all connected SSE clients."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    async def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._clients):
            await q.put(event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mcp = BankingMCP()
    app.state.bus = Broadcaster()
    await app.state.mcp.connect()
    yield
    await app.state.mcp.close()


app = FastAPI(title="Dialogflow CX Banking Prototype", lifespan=lifespan)

# CX webhooks come from Google's servers; allow all origins for the prototype.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


async def _snapshot() -> dict[str, Any]:
    """Full dashboard state pulled from the MCP server."""
    accounts = await app.state.mcp.get_accounts()
    txns = await app.state.mcp.get_transactions(limit=25)
    return {"accounts": accounts.get("accounts", []), "transactions": txns.get("transactions", [])}


async def _broadcast_state(action: str | None = None) -> None:
    snap = await _snapshot()
    snap["action"] = action
    await app.state.bus.publish(snap)


# ---------------------------------------------------------------------------
# Website + state APIs
# ---------------------------------------------------------------------------

@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(await _snapshot())


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    q = app.state.bus.subscribe()

    async def gen():
        try:
            # Send the current state immediately on connect.
            yield f"data: {json.dumps(await _snapshot())}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            app.state.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Dialogflow CX webhook
# ---------------------------------------------------------------------------

def _reply(text: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"fulfillment_response": {"messages": [{"text": {"text": [text]}}]}}
    if params:
        body["sessionInfo"] = {"parameters": params}
    return body


def _num(value: Any) -> float | None:
    if value is None:
        return None
    # CX @sys.number arrives as float/int; amounts may also arrive as strings.
    try:
        if isinstance(value, dict):  # @sys.unit-currency -> {"amount": .., "currency": ..}
            value = value.get("amount")
        return float(value)
    except (TypeError, ValueError):
        return None


@app.post("/webhook")
async def cx_webhook(request: Request) -> JSONResponse:
    payload = await request.json()
    return JSONResponse(await handle_cx(payload))


@app.post("/")
async def cx_webhook_root(request: Request) -> JSONResponse:
    """Also accept the CX webhook at the root, in case the configured webhook
    URL omits the /webhook path (a common ngrok-URL copy/paste mistake)."""
    payload = await request.json()
    return JSONResponse(await handle_cx(payload))


async def handle_cx(payload: dict[str, Any]) -> dict[str, Any]:
    tag = (payload.get("fulfillmentInfo") or {}).get("tag", "")
    params = (payload.get("sessionInfo") or {}).get("parameters") or {}
    mcp = app.state.mcp

    if tag == "check_balance":
        account = params.get("account")
        data = await mcp.get_accounts()
        accounts = data.get("accounts", [])
        if account:
            accounts = [a for a in accounts if account.lower() in (a["name"].lower(), a["type"], a["id"])
                        or account.lower() in a["name"].lower()]
        if not accounts:
            return _reply("I couldn't find that account.")
        lines = [f"{a['name']}: ${a['balance']:,.2f}" for a in accounts]
        return _reply("Here are your balances:\n" + "\n".join(lines))

    if tag == "list_transactions":
        account = params.get("account")
        data = await mcp.get_transactions(account_id=account, limit=5)
        rows = data.get("transactions", [])
        if not rows:
            return _reply("No recent transactions found.")
        lines = [f"{t['date']}  {t['description']}  ${t['amount']:,.2f}" for t in rows]
        return _reply("Your recent transactions:\n" + "\n".join(lines))

    if tag == "transfer_funds":
        amount = _num(params.get("amount"))
        result = await mcp.transfer_funds(
            from_account=params.get("from_account") or "checking",
            to_account=params.get("to_account") or "savings",
            amount=amount or 0,
        )
        if result.get("ok"):
            await _broadcast_state("transfer_funds")
            return _reply(result["message"])
        return _reply(f"Sorry, I couldn't complete the transfer. {result.get('error', '')}")

    if tag == "pay_bill":
        amount = _num(params.get("amount"))
        result = await mcp.pay_bill(
            account_id=params.get("from_account") or params.get("account") or "checking",
            payee=params.get("payee") or "your payee",
            amount=amount or 0,
        )
        if result.get("ok"):
            await _broadcast_state("pay_bill")
            return _reply(result["message"])
        return _reply(f"Sorry, I couldn't pay that bill. {result.get('error', '')}")

    return _reply("I'm not sure how to help with that yet.")


# ---------------------------------------------------------------------------
# Local test hooks (let you demo the full loop without a live CX agent)
# ---------------------------------------------------------------------------

@app.post("/api/simulate")
async def simulate(request: Request) -> JSONResponse:
    """Mimic a CX webhook call so you can exercise MCP + live updates locally."""
    body = await request.json()
    fake_cx = {
        "fulfillmentInfo": {"tag": body.get("tag", "")},
        "sessionInfo": {"parameters": body.get("parameters", {})},
    }
    return JSONResponse(await handle_cx(fake_cx))


# ---------------------------------------------------------------------------
# Playbook Tools (REST / OpenAPI)
#
# These are what a Dialogflow CX *Playbook* calls. Unlike the flow webhook
# (tag + sessionInfo envelope), an OpenAPI Tool is plain REST: the generative
# agent fills in the parameters and calls the operation directly. Same MCP
# tools underneath, same live dashboard push — this is the "not purely scripted"
# half of the hybrid agent.
# ---------------------------------------------------------------------------

class TransferBody(BaseModel):
    from_account: str = Field(..., description="Source account: checking, savings, or credit")
    to_account: str = Field(..., description="Destination account: checking, savings, or credit")
    amount: float = Field(..., gt=0, description="Amount in USD, positive")


class PayBillBody(BaseModel):
    from_account: str = Field("checking", description="Account to pay from")
    payee: str = Field(..., description="Name of the biller, e.g. 'Electric Company'")
    amount: float = Field(..., gt=0, description="Amount in USD, positive")


@app.get("/tools/accounts", operation_id="get_accounts", summary="List the customer's accounts and balances")
async def tool_get_accounts() -> dict[str, Any]:
    return await app.state.mcp.get_accounts()


@app.get("/tools/transactions", operation_id="get_transactions", summary="List recent transactions")
async def tool_get_transactions(account_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    return await app.state.mcp.get_transactions(account_id=account_id, limit=limit)


@app.post("/tools/transfer", operation_id="transfer_funds", summary="Transfer money between the customer's accounts")
async def tool_transfer(body: TransferBody) -> dict[str, Any]:
    result = await app.state.mcp.transfer_funds(
        from_account=body.from_account, to_account=body.to_account, amount=body.amount,
    )
    if result.get("ok"):
        await _broadcast_state("transfer_funds")
    return result


@app.post("/tools/pay-bill", operation_id="pay_bill", summary="Pay a bill to a payee")
async def tool_pay_bill(body: PayBillBody) -> dict[str, Any]:
    result = await app.state.mcp.pay_bill(
        account_id=body.from_account, payee=body.payee, amount=body.amount,
    )
    if result.get("ok"):
        await _broadcast_state("pay_bill")
    return result


# Static site (mounted last so /api, /webhook and /tools win) ---------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8099")), reload=True)
