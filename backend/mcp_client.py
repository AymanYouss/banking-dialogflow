"""
Thin MCP client used by the FastAPI backend.

Spawns backend/mcp_server.py as a subprocess over stdio and keeps one long-lived
session for the app's lifetime. All banking actions the webhook performs go
through here, so the MCP server stays the single source of truth.
"""

from __future__ import annotations

import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = Path(__file__).with_name("mcp_server.py")


class BankingMCP:
    """Owns the stdio connection to the banking MCP server."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def connect(self) -> None:
        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        """Call an MCP tool and return its structured JSON result."""
        if self._session is None:
            raise RuntimeError("MCP session not connected")
        result = await self._session.call_tool(tool, arguments)
        # FastMCP returns tool output as text content containing JSON.
        for block in result.content:
            if getattr(block, "type", None) == "text":
                try:
                    return json.loads(block.text)
                except json.JSONDecodeError:
                    return {"text": block.text}
        return {}

    # Convenience wrappers -------------------------------------------------
    async def get_accounts(self) -> dict[str, Any]:
        return await self.call("get_accounts")

    async def get_transactions(self, account_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        return await self.call("get_transactions", account_id=account_id, limit=limit)

    async def transfer_funds(self, from_account: str, to_account: str, amount: float) -> dict[str, Any]:
        return await self.call("transfer_funds", from_account=from_account, to_account=to_account, amount=amount)

    async def pay_bill(self, account_id: str, payee: str, amount: float) -> dict[str, Any]:
        return await self.call("pay_bill", account_id=account_id, payee=payee, amount=amount)
