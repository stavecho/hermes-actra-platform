import weakref
from typing import Any

from .protocol import is_tool_error_status

_ACTIVE_ADAPTERS = weakref.WeakSet()


def register_active_adapter(adapter) -> None:
    _ACTIVE_ADAPTERS.add(adapter)


def unregister_active_adapter(adapter) -> None:
    _ACTIVE_ADAPTERS.discard(adapter)


def _emit_tool_event_to_active_adapters(
    *,
    phase: str,
    tool_name: str,
    tool_call_id: str = "",
    session_id: str = "",
    is_error: bool = False,
) -> None:
    for adapter in list(_ACTIVE_ADAPTERS):
        adapter.emit_tool_event_soon(
            phase=phase,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            session_id=session_id,
            is_error=is_error,
        )


def _on_pre_tool_call(
    *,
    tool_name: str = "",
    tool_call_id: str = "",
    session_id: str = "",
    **_: Any,
) -> None:
    _emit_tool_event_to_active_adapters(
        phase="start",
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        session_id=session_id,
    )


def _on_post_tool_call(
    *,
    tool_name: str = "",
    tool_call_id: str = "",
    session_id: str = "",
    status: str = "",
    error_type: str | None = None,
    error_message: str | None = None,
    **_: Any,
) -> None:
    _emit_tool_event_to_active_adapters(
        phase="result",
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        session_id=session_id,
        is_error=is_tool_error_status(status, error_type, error_message),
    )


def register_hooks(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
