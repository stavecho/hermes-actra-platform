"""Check the installed distribution and Hermes integration without network access."""

import asyncio
import json
import os
from importlib import metadata, resources
from pathlib import Path
from unittest.mock import AsyncMock

from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry
from hermes_cli.plugins import PluginManager

import actra
from actra.hooks import register_active_adapter, unregister_active_adapter


class RecordingSocket:
    def __init__(self):
        self.frames = asyncio.Queue()

    async def send(self, raw):
        self.frames.put_nowait(json.loads(raw))

    async def close(self):
        pass

    async def expect(self, event):
        frame = await asyncio.wait_for(self.frames.get(), timeout=2)
        assert frame["event"] == event, frame
        return frame


async def check_message_flow(adapter, manager):
    socket = RecordingSocket()
    adapter.ws = socket
    adapter._loop = asyncio.get_running_loop()
    adapter._running = True
    adapter.end_idle_seconds = 0.001
    adapter.handle_message = AsyncMock()
    register_active_adapter(adapter)
    try:
        await adapter._handle_ws_message(json.dumps({
            "event": "messages", "data": "hello", "chat_id": "compat-chat",
            "message_id": "compat-1", "user_id": "compat-user",
        }))
        adapter.handle_message.assert_awaited_once()
        message = adapter.handle_message.await_args.args[0]
        assert message.text == "hello" and message.message_id == "compat-1"
        assert message.source.platform.value == "actra"
        session_id = adapter._session_key(message.source)

        manager.invoke_hook("pre_tool_call", tool_name="read_file",
                            tool_call_id="tool-1", session_id=session_id)
        assert (await socket.expect("start"))["message_id"] == "compat-1"
        started = await socket.expect("tools")
        assert started["phase"] == "start" and started["toolCallId"] == "tool-1"
        manager.invoke_hook("post_tool_call", tool_name="read_file",
                            tool_call_id="tool-1", session_id=session_id, status="success")
        finished = await socket.expect("tools")
        assert finished["phase"] == "result" and not finished["isError"]

        result = await adapter.send("compat-chat", "reply", metadata={"notify": True})
        assert result.success
        reply = await socket.expect("messages")
        assert reply["message_id"] == "compat-1" and reply["data"] == "reply"
        assert (await socket.expect("end"))["message_id"] == "compat-1"

        await adapter._handle_ws_message(json.dumps({
            "event": "messages", "data": "interrupt me", "chat_id": "compat-chat",
            "message_id": "compat-2", "user_id": "compat-user",
        }))
        await adapter._handle_ws_message(json.dumps({
            "event": "messages", "break": True, "chat_id": "compat-chat",
            "user_id": "compat-user",
        }))
        assert (await socket.expect("break"))["data"]["message_ids"] == ["compat-2"]
        assert adapter.handle_message.await_count == 2
        await adapter._handle_ws_message('{"event": "PING", "data": 42}')
        assert (await socket.expect("PING"))["data"] == 42
        assert socket.frames.empty()
    finally:
        unregister_active_adapter(adapter)
        await adapter.disconnect()


def main() -> None:
    distribution = metadata.distribution("hermes-actra-platform")
    manifest = resources.files("actra").joinpath("plugin.yaml").read_text()
    assert f"version: {distribution.version}" in manifest.splitlines(), "plugin version mismatch"
    assert distribution.metadata["License-Expression"] == "Apache-2.0"
    entries = [
        ep for ep in distribution.entry_points
        if ep.group == "hermes_agent.plugins" and ep.name == "actra"
    ]
    assert len(entries) == 1, "missing or duplicate plugin entry point"
    assert entries[0].load() is actra, "entry point does not resolve to actra"

    manager = PluginManager()
    manifests = [m for m in manager._scan_entry_points() if m.name == "actra"]
    assert len(manifests) == 1, "Hermes did not discover the installed plugin"
    manager._load_plugin(manifests[0])
    loaded = manager._plugins["actra"]
    assert loaded.enabled and not loaded.error, loaded.error
    assert set(loaded.hooks_registered) == {"pre_tool_call", "post_tool_call"}

    entry = platform_registry.get("actra")
    assert entry is not None, "Actra platform was not registered"
    config = PlatformConfig(enabled=True, extra={
        "ws_url": "wss://example.com/ws", "token": "test-token",
    })
    adapter = entry.adapter_factory(config)
    assert adapter.platform.value == "actra"
    assert entry.check_fn() and entry.validate_config(config)
    hermes_version = metadata.version("hermes-agent")
    expected = os.getenv("EXPECTED_HERMES_VERSION")
    assert not expected or hermes_version == expected, (hermes_version, expected)
    asyncio.run(check_message_flow(adapter, manager))
    print(f"Installed hermes-actra-platform {distribution.version}: {Path(actra.__file__)}")
    print(f"Hermes {hermes_version}: manifest, entry point, hooks and platform registration: OK")
    print("Inbound messages, tool hooks, replies, end, interruption and PING: OK")


if __name__ == "__main__":
    main()
