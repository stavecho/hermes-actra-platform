# hermes-actra-platform

Hermes Agent 的 Actra message platform 插件。插件通过 WebSocket 接收 Actra 消息事件，把它们转换成 Hermes `MessageEvent`，并把 Hermes 回复、工具调用状态和中断状态转换回 Actra 事件。

## 功能

- WebSocket 长连接接入 Actra 服务。
- 入站 `messages` 事件转换为 Hermes 文本消息。
- 出站回复按 `start -> messages/tools -> end` 事件序列返回。
- Hermes 工具调用通过 `pre_tool_call` / `post_tool_call` hook 转成 `tools` 事件。
- 支持 `break: true` 中断当前 Hermes session，并返回被打断的 `message_ids`。
- 支持 `PING` 保活和回显。

## 安装

在你的 Hermes conda 环境中安装：

```bash
conda activate hermes-dev
pip install -e .
```

当前 Hermes 的 `hermes plugins list/enable` 主要扫描 `~/.hermes/plugins` 目录，不一定会显示 pip entry point 插件。源码开发时可以把插件目录软链过去：

```bash
mkdir -p ~/.hermes/plugins
ln -sfn /Users/sakura/develop/hermes-actra-platform/actra ~/.hermes/plugins/actra
hermes plugins list --plain --no-bundled
hermes plugins enable actra
```

如果只使用 pip entry point，需要确保 Hermes config 中启用插件：

```yaml
plugins:
  enabled:
    - actra
```

## 环境变量

必需：

```bash
export ACTRA_WS_URL='wss://example.com/ws'
export ACTRA_WS_TOKEN='token'
```

可选：

```bash
export ACTRA_WS_HOME_CHANNEL='actra-default'
export ACTRA_APPROVE='true'
export ACTRA_END_IDLE_SECONDS='1'
```

如果 `ACTRA_WS_URL` 没有 query string，插件会自动拼接：

```text
?service_type=service&token=<ACTRA_WS_TOKEN>
```

如果 URL 已经包含 `?`，插件会原样使用，不再追加 token。

也可以通过 Hermes `config.yaml` 的 `platforms.actra.extra` 配置：

```yaml
plugins:
  enabled:
    - actra

platforms:
  actra:
    enabled: true
    extra:
      ws_url: ws://192.168.1.231:8080/ws/ibuddi
      token: your-token
      approve: true
      end_idle_seconds: 1
```

`approve: true` 只对 Actra 平台生效。它不会修改 Hermes 全局 `approvals.mode`，而是在 Actra 收到危险命令审批请求时自动按 `/approve always` 处理。如果不设置，则按 Hermes 全局审批配置继续运行。

`end_idle_seconds` 是最终 `messages` 后发送 `end` 的等待时间，默认 1 秒。Hermes 的最终回复会通过 `metadata.notify=true` 标记；工具结果本身不会触发 `end`，所以 `tools` 和最终 `messages` 会落在同一个 `start/end` 中。

## 入站事件

Actra 服务端发给 Hermes 插件：

```json
{
  "event": "messages",
  "data": "你好",
  "message_id": "msg-001",
  "chat_id": "chat-001",
  "account_id": "default",
  "user_id": "client",
  "user_name": "client"
}
```

字段说明：

- `event`: 必须是 `messages`。
- `data`: 用户文本。
- `message_id`: 可选，优先使用；兼容 `messageId`。
- `chat_id`: 可选。缺省时使用 `actra-<account_id>`，`account_id` 缺省为 `default`。
- `break`: 可选，`true` 时中断当前 session。

## 出站回复事件

Hermes 回复会按同一个 `message_id` 返回：

```json
{
  "event": "start",
  "from": "service",
  "message_id": "msg-001",
  "messageId": "msg-001",
  "query": "你好"
}
```

```json
{
  "event": "messages",
  "from": "service",
  "data": "你好，我是 Hermes。",
  "query": "你好",
  "message_id": "msg-001",
  "messageId": "msg-001"
}
```

```json
{
  "event": "end",
  "from": "service",
  "query": "你好",
  "message_id": "msg-001",
  "messageId": "msg-001"
}
```

`tools` 事件会出现在 `start` 和 `end` 之间。

## 工具事件

工具开始：

```json
{
  "event": "tools",
  "from": "service",
  "message_id": "msg-001",
  "messageId": "msg-001",
  "query": "读一下 README",
  "type": "call",
  "role": "assistant",
  "title": "tool read_file",
  "phase": "start",
  "name": "read_file",
  "toolCallId": "tool-001",
  "isError": false
}
```

工具结果：

```json
{
  "event": "tools",
  "from": "service",
  "message_id": "msg-001",
  "messageId": "msg-001",
  "query": "读一下 README",
  "type": "output",
  "role": "tool",
  "title": "Tool output read_file",
  "phase": "result",
  "name": "read_file",
  "toolCallId": "tool-001",
  "isError": false
}
```

错误、阻断和取消会把 `isError` 设置为 `true`。

## 中断事件

发送 break-only：

```json
{
  "event": "messages",
  "break": true
}
```

插件会取消当前 session，并返回被打断的消息 ID：

```json
{
  "event": "break",
  "data": {
    "message_ids": ["msg-001", "msg-002"]
  }
}
```

如果 `break: true` 同时带了新的 `data`，插件会先返回 `break` 事件，再把新文本作为新一轮消息处理。

## PING

插件每 30 秒主动发送：

```json
{
  "event": "PING",
  "data": 0
}
```

收到 Actra 服务端的 `PING` 时会回显：

```json
{
  "event": "PING",
  "data": "原始 data"
}
```

## 代码结构

- `actra/adapter.py`: Hermes platform adapter，负责 WebSocket 连接和 Hermes message 转换。
- `actra/protocol.py`: Actra 事件协议构造和解析。
- `actra/hooks.py`: Hermes tool lifecycle hooks，负责把工具调用转换成 `tools` 事件。
- `actra/plugin.yaml`: Hermes plugin manifest。

## 开发检查

```bash
conda run -n hermes-dev python -m py_compile actra/adapter.py actra/hooks.py actra/protocol.py actra/__init__.py
conda run -n hermes-dev python -m ruff check actra
```

验证 Hermes 能加载插件：

```bash
conda run -n hermes-dev python -c "from hermes_cli import plugins as p; p._get_enabled_plugins=lambda:{'actra'}; m=p.PluginManager(); m.discover_and_load(force=True); print(m._plugins.get('actra'))"
```
