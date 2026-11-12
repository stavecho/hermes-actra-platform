"""Check the installed distribution and Hermes integration without network access."""

from importlib import metadata, resources
from pathlib import Path

from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry
from hermes_cli.plugins import PluginManager

import actra


def main() -> None:
    distribution = metadata.distribution("hermes-actra-platform")
    manifest = resources.files("actra").joinpath("plugin.yaml").read_text()
    assert f"version: {distribution.version}" in manifest, "plugin version mismatch"
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
    print(f"Installed hermes-actra-platform {distribution.version}: {Path(actra.__file__)}")
    print("Manifest, entry point, Hermes hooks, platform registration and adapter: OK")


if __name__ == "__main__":
    main()
