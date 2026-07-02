import time
from urllib.parse import quote


def generated_message_id() -> str:
    return f"actra-{int(time.time() * 1000)}"


def resolve_ws_url(ws_base: str | None, token: str | None) -> str:
    base = str(ws_base or "").rstrip("/")
    if not base:
        return ""
    if "?" in base:
        return base
    return f"{base}?service_type=service&token={quote(str(token or ''))}"


def resolve_inbound_message_id(data: dict) -> str:
    for key in ("message_id", "messageId"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return generated_message_id()


def chat_id_for(data: dict) -> str:
    chat_id = data.get("chat_id")
    if isinstance(chat_id, str) and chat_id.strip():
        return chat_id.strip()
    account_id = data.get("account_id") or "default"
    return f"actra-{account_id}"


def inbound_text(data: dict) -> str:
    text = data.get("data")
    if not isinstance(text, str) or not text.strip():
        text = data.get("query")
    return text.strip() if isinstance(text, str) else ""


def ping_event(data=None) -> dict:
    return {"event": "PING", "data": data}


def start_event(*, message_id: str, query: str) -> dict:
    return {
        "event": "start",
        "from": "service",
        "message_id": message_id,
        "messageId": message_id,
        "query": query,
    }


def message_event(*, message_id: str, query: str, text: str) -> dict:
    return {
        "event": "messages",
        "from": "service",
        "data": text,
        "query": query,
        "message_id": message_id,
        "messageId": message_id,
    }


def end_event(*, message_id: str, query: str) -> dict:
    return {
        "event": "end",
        "from": "service",
        "query": query,
        "message_id": message_id,
        "messageId": message_id,
    }


def break_event(message_ids: list[str]) -> dict:
    return {
        "event": "break",
        "data": {
            "message_ids": message_ids,
        },
    }


def tool_event(
    *,
    message_id: str,
    query: str,
    phase: str,
    tool_name: str,
    tool_call_id: str = "",
    is_error: bool = False,
) -> dict:
    name = str(tool_name or "tool").strip() or "tool"
    is_output = phase in {"update", "result"}
    return {
        "event": "tools",
        "from": "service",
        "message_id": message_id,
        "messageId": message_id,
        "query": query,
        "type": "output" if is_output else "call",
        "role": "tool" if is_output else "assistant",
        "title": f"Tool output {name}" if is_output else f"tool {name}",
        "phase": phase,
        "name": name,
        "toolCallId": str(tool_call_id or ""),
        "isError": bool(is_error),
    }


def is_tool_error_status(status: str, error_type: str | None, error_message: str | None) -> bool:
    return bool(error_type or error_message or status in {"error", "blocked", "cancelled"})
