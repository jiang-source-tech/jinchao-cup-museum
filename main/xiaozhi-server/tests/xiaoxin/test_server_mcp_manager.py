import pytest

from core.providers.tools.server_mcp import mcp_manager


class _FakeClient:
    def __init__(self, config):
        self.config = config
        self.cleaned = False

    async def initialize(self, logging_callback=None):
        return None

    def get_available_tools(self):
        return []

    async def cleanup(self):
        self.cleaned = True


@pytest.mark.asyncio
async def test_server_mcp_uses_configured_initialization_timeout(monkeypatch):
    observed = {}

    async def fake_wait_for(awaitable, timeout):
        observed["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(mcp_manager, "ServerMCPClient", _FakeClient)
    monkeypatch.setattr(mcp_manager.asyncio, "wait_for", fake_wait_for)

    manager = mcp_manager.ServerMCPManager(conn=None)
    await manager._init_server(
        "weather",
        {
            "command": "npx",
            "init_timeout_seconds": 90,
        },
    )

    assert observed["timeout"] == 90
