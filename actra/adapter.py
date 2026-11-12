import asyncio
import json
import logging
import os

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    SessionSource,
)
from gateway.session import build_session_key

from .hooks import register_active_adapter, register_hooks, unregister_active_adapter
from .protocol import (
    break_event,
    chat_id_for,
    end_event,
    generated_message_id,
    inbound_text,
    message_event,
    ping_event,
    resolve_inbound_message_id,
    resolve_ws_url,
    start_event,
    tool_event,
)

logger = logging.getLogger(__name__)
DEFAULT_END_IDLE_SECONDS = 1.0


class ActraAdapter(BasePlatformAdapter):
    def __init__(self, config: PlatformConfig):
        self.platform = Platform("actra")
        super().__init__(config, self.platform)

        extra = config.extra or {}
        self.ws_base = os.getenv("ACTRA_WS_URL") or extra.get("ws_url")
        self.token = os.getenv("ACTRA_WS_TOKEN") or extra.get("token")
        self.ws_url = resolve_ws_url(self.ws_base, self.token)
        self.end_idle_seconds = _float_config(
            os.getenv("ACTRA_END_IDLE_SECONDS"),
            extra.get("end_idle_seconds"),
            DEFAULT_END_IDLE_SECONDS,
        )
        self.suppress_internal_notices = _bool_config(
            os.getenv("ACTRA_SUPPRESS_INTERNAL_NOTICES"),
            extra.get("suppress_internal_notices"),
            True,
        )
        self.auto_approve = _bool_config(
            os.getenv("ACTRA_APPROVE"),
            extra.get("approve"),
            False,
        )

        self.ws = None
        self._task = None
        self._ping_task = None
        self._loop = None
        self._running = False
        self._contexts_by_chat = {}
        self._contexts_by_session = {}
        self._latest_context = {}
        self._message_ids_by_session = {}
        self._end_tasks = {}
        self._started_messages = set()
        self._active_tools_by_message = {}

    async def connect(self, **kwargs) -> bool:
        if not self.ws_base:
            logger.error("ACTRA_WS_URL is required")
            return False
        if not self.token:
            logger.error("ACTRA_WS_TOKEN is required")
            return False

        self._running = True
        self._loop = asyncio.get_running_loop()
        register_active_adapter(self)
        self._task = asyncio.create_task(self._run_loop())
        self._mark_connected()
        logger.info("actra adapter started")
        return True

    async def disconnect(self) -> None:
        self._running = False
        unregister_active_adapter(self)

        if self.ws:
            await self.ws.close()
        if self._task:
            self._task.cancel()
        if self._ping_task:
            self._ping_task.cancel()
        for task in self._end_tasks.values():
            task.cancel()
        self._end_tasks.clear()
        self._started_messages.clear()
        self._active_tools_by_message.clear()

        self._mark_disconnected()
        logger.info("actra adapter stopped")

    async def _run_loop(self):
        import websockets

        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    logger.info("actra websocket connected")
                    self._start_ping()

                    async for raw in ws:
                        await self._handle_ws_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("actra websocket disconnected; retrying in 3s")
                await asyncio.sleep(3)
            finally:
                if self._ping_task:
                    self._ping_task.cancel()
                    self._ping_task = None
                self.ws = None

    def _start_ping(self):
        if self._ping_task:
            self._ping_task.cancel()
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def _ping_loop(self):
        while self._running:
            await asyncio.sleep(30)
            await self._send_socket_event(ping_event(0))

    async def _send_socket_event(self, payload: dict) -> bool:
        if not self.ws:
            return False
        await self.ws.send(json.dumps(payload, ensure_ascii=False))
        return True

    async def _handle_ws_message(self, raw: str):
        data = json.loads(raw)
        event_name = data.get("event")

        if event_name == "PING":
            await self._send_socket_event(ping_event(data.get("data")))
            return
        if event_name != "messages":
            logger.debug("ignore non-message frame: %s", data)
            return

        text = inbound_text(data)
        if data.get("break") is True:
            interrupted_ids = await self._break_chat(data)
            await self._send_socket_event(break_event(interrupted_ids))
            if not text:
                logger.info("actra break-only event received")
                return

        source = self._source_from_payload(data)
        message_id = resolve_inbound_message_id(data)
        context = {"message_id": message_id, "query": text}
        self._record_context(source, context)

        await self.handle_message(
            MessageEvent(
                text=text,
                message_type=MessageType.TEXT,
                source=source,
                message_id=message_id,
                raw_message=data,
            )
        )

    def _source_from_payload(self, data: dict) -> SessionSource:
        return SessionSource(
            platform=self.platform,
            chat_id=chat_id_for(data),
            chat_type="dm",
            chat_name=data.get("chat_name") or "actra client",
            user_id=str(data.get("user_id") or "client"),
            user_name=data.get("user_name") or "client",
            thread_id=data.get("thread_id"),
            chat_topic=data.get("chat_topic"),
        )

    def _record_context(self, source: SessionSource, context: dict) -> None:
        session_key = self._session_key(source)
        message_id = context.get("message_id")
        self._contexts_by_chat[source.chat_id] = context
        self._contexts_by_session[session_key] = context
        self._latest_context = context
        if message_id:
            message_ids = self._message_ids_by_session.setdefault(session_key, [])
            if message_id not in message_ids:
                message_ids.append(message_id)

    def _session_key(self, source: SessionSource) -> str:
        return build_session_key(
            source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )

    async def _break_chat(self, data: dict) -> list[str]:
        session_key = self._session_key(self._source_from_payload(data))
        message_ids = list(self._message_ids_by_session.pop(session_key, []))
        for message_id in message_ids:
            self._cancel_scheduled_end(message_id)
            self._started_messages.discard(message_id)
            self._active_tools_by_message.pop(message_id, None)
        await self.cancel_session_processing(session_key, discard_pending=True)
        return message_ids

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if not self.ws:
            return SendResult(success=False, error="WebSocket is not connected")

        text = str(content or "")
        if not text.strip():
            return SendResult(success=True)
        if self.suppress_internal_notices and _is_internal_notice(text):
            return SendResult(success=True)

        context = self._contexts_by_chat.get(str(chat_id), {})
        message_id = context.get("message_id") or str(reply_to or "") or generated_message_id()
        query = context.get("query") or ""

        await self._send_start_once(message_id, query)
        await self._send_socket_event(message_event(message_id=message_id, query=query, text=text))
        if self._should_end_after_send(metadata) and not self._has_active_tool(message_id):
            self._schedule_end(message_id, query)

        return SendResult(success=True, message_id=message_id)

    async def send_typing(self, chat_id: str, **kwargs):
        if not self.ws:
            return

        context = self._contexts_by_chat.get(str(chat_id), {})
        await self._send_start_once(
            context.get("message_id") or generated_message_id(),
            context.get("query") or "",
        )

    async def get_chat_info(self, chat_id: str) -> dict:
        return {
            "name": str(chat_id or "actra"),
            "type": "dm",
            "platform": "actra",
        }

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata=None,
    ) -> SendResult:
        del chat_id, command, description, metadata

        if not self.auto_approve:
            return SendResult(success=False, error="actra auto approval is disabled")

        from tools.approval import resolve_gateway_approval

        resolved = resolve_gateway_approval(session_key, "always")
        if resolved < 1:
            return SendResult(success=False, error="no pending approval to resolve")
        logger.info("actra auto-approved %s pending command approval(s)", resolved)
        return SendResult(success=True)

    async def _send_start_once(self, message_id: str, query: str) -> None:
        if message_id in self._started_messages:
            return
        self._started_messages.add(message_id)
        await self._send_socket_event(start_event(message_id=message_id, query=query))

    def _schedule_end(self, message_id: str, query: str, delay: float | None = None) -> None:
        self._cancel_scheduled_end(message_id)
        self._end_tasks[message_id] = asyncio.create_task(
            self._send_end_later(
                message_id,
                query,
                self.end_idle_seconds if delay is None else delay,
            )
        )

    def _cancel_scheduled_end(self, message_id: str) -> None:
        existing = self._end_tasks.pop(message_id, None)
        if existing:
            existing.cancel()

    async def _send_end_later(self, message_id: str, query: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self._send_socket_event(end_event(message_id=message_id, query=query))
            self._started_messages.discard(message_id)
            self._forget_message_id(message_id)
        except asyncio.CancelledError:
            raise
        finally:
            self._end_tasks.pop(message_id, None)

    def _forget_message_id(self, message_id: str) -> None:
        for session_key, message_ids in list(self._message_ids_by_session.items()):
            if message_id not in message_ids:
                continue
            message_ids[:] = [value for value in message_ids if value != message_id]
            if not message_ids:
                self._message_ids_by_session.pop(session_key, None)

    def emit_tool_event_soon(
        self,
        *,
        phase: str,
        tool_name: str,
        tool_call_id: str = "",
        session_id: str = "",
        is_error: bool = False,
    ) -> None:
        if not self._loop or not self._running:
            return

        context = self._contexts_by_session.get(session_id) or self._latest_context
        if not context:
            return

        message_id = context.get("message_id") or generated_message_id()
        query = context.get("query") or ""
        payload = tool_event(
            message_id=message_id,
            query=query,
            phase=phase,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            is_error=is_error,
        )
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(
                self._send_tool_event(
                    message_id,
                    query,
                    payload,
                    phase=phase,
                    tool_call_id=tool_call_id,
                )
            )
        )

    async def _send_tool_event(
        self,
        message_id: str,
        query: str,
        payload: dict,
        *,
        phase: str,
        tool_call_id: str,
    ) -> None:
        if phase == "start":
            self._mark_tool_active(message_id, tool_call_id)
            self._cancel_scheduled_end(message_id)
        await self._send_start_once(message_id, query)
        await self._send_socket_event(payload)
        if phase in {"result", "update"}:
            self._mark_tool_inactive(message_id, tool_call_id)

    def _should_end_after_send(self, metadata) -> bool:
        if metadata is None:
            return True
        if not isinstance(metadata, dict):
            return False
        if metadata.get("expect_edits"):
            return False
        return bool(metadata.get("notify"))

    def _tool_key(self, tool_call_id: str) -> str:
        return str(tool_call_id or "__unknown_tool__")

    def _mark_tool_active(self, message_id: str, tool_call_id: str) -> None:
        active_tools = self._active_tools_by_message.setdefault(message_id, set())
        active_tools.add(self._tool_key(tool_call_id))

    def _mark_tool_inactive(self, message_id: str, tool_call_id: str) -> None:
        active_tools = self._active_tools_by_message.get(message_id)
        if not active_tools:
            return
        active_tools.discard(self._tool_key(tool_call_id))
        if not active_tools:
            self._active_tools_by_message.pop(message_id, None)

    def _has_active_tool(self, message_id: str) -> bool:
        return bool(self._active_tools_by_message.get(message_id))


def check_requirements():
    try:
        import websockets  # noqa: F401
    except ImportError:
        return False
    return True


def _float_config(env_value, config_value, default: float) -> float:
    for value in (env_value, config_value):
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return default


def _bool_config(env_value, config_value, default: bool) -> bool:
    for value in (env_value, config_value):
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _is_internal_notice(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.startswith("ℹ Codex ")
        and "auto-compaction was raised" in stripped
    ) or stripped.startswith("📬 No home channel is set for Actra.")


def validate_config(config: PlatformConfig) -> bool:
    extra = config.extra or {}
    ws_url = os.getenv("ACTRA_WS_URL") or extra.get("ws_url")
    token = os.getenv("ACTRA_WS_TOKEN") or extra.get("token")
    return bool(ws_url and token)


def register(ctx):
    register_hooks(ctx)
    ctx.register_platform(
        name="actra",
        label="actra",
        adapter_factory=lambda cfg: ActraAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["ACTRA_WS_URL", "ACTRA_WS_TOKEN"],
        cron_deliver_env_var="ACTRA_WS_HOME_CHANNEL",
        emoji="🔌",
        platform_hint="You are chatting through a custom actra channel.",
    )
