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

需要 Python 3.11–3.13、Hermes Agent 0.17.x，以及可访问的 Actra WebSocket 服务。请在运行 Hermes 的同一个 Python 环境中安装插件。

PyPI 首次发布完成后，可使用：

```bash
python -m pip install hermes-actra-platform
```

首次发布前，或需要安装 GitHub 上的版本时：

```bash
python -m pip install "git+https://github.com/stavecho/hermes-actra-platform.git"
```

插件通过 `hermes_agent.plugins` entry point 加载，不需要软链到 `~/.hermes/plugins`。在 Hermes 的 `~/.hermes/config.yaml` 中合并以下配置（保留已有插件和平台配置）：

```yaml
plugins:
  enabled:
    - actra

platforms:
  actra:
    enabled: true
```

配置下方必需的环境变量后，启动或重启 Hermes gateway：

```bash
hermes gateway
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
export ACTRA_APPROVE='false'
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
      ws_url: wss://example.com/ws
      token: your-token
      approve: false
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

从源码安装：

```bash
git clone https://github.com/stavecho/hermes-actra-platform.git
cd hermes-actra-platform
python -m pip install -e .
```

在 Hermes 所在的 Python 环境中检查：

```bash
python -m compileall -q actra
python scripts/check_install.py
```

`check_install.py` 验证安装的分发包、插件资源、pip entry point、Hermes 平台注册和 adapter 初始化；不连接 Actra 服务。

## 发布

构建和检查：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

建议使用单独的构建环境，避免构建工具与 Hermes 的固定版本依赖发生冲突。GitHub Actions 在 push 和 pull request 时构建分发包，并在另一个虚拟环境中验证 wheel 安装。

自动发布使用 PyPI Trusted Publishing，不需要在仓库中保存 API Token。首次发布前：

1. 注册并验证 [PyPI](https://pypi.org/account/register/) 账号，启用双因素认证。
2. 在仓库 **Settings → Environments** 创建 `pypi` environment，并配置发布审批人。
3. 在 [PyPI Publishing](https://pypi.org/manage/account/publishing/) 添加 Pending Publisher，填写：

   | 字段 | 值 |
   | --- | --- |
   | PyPI Project Name | `hermes-actra-platform` |
   | GitHub Owner | `stavecho` |
   | GitHub Repository | `hermes-actra-platform` |
   | Workflow filename | `publish.yml` |
   | Environment name | `pypi` |

4. 创建指向准备发布的提交的 GitHub Release，例如 `v0.1.0`。标签去掉 `v` 后必须与 `pyproject.toml` 的版本一致。
5. 发布 Release 后，`Publish to PyPI` 工作流会检查版本、构建、验证 wheel 安装，然后上传 PyPI；若 environment 配置了审批，需要先批准发布。

后续发布时，同时更新 `pyproject.toml` 和 `actra/plugin.yaml` 的版本，并使用新的 Release 标签。每次手动构建前清理旧的 `dist/` 产物，避免混入旧版本。

参考：[PyPA GitHub Actions 发布指南](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)。

## 许可证

本项目使用 [Apache License 2.0](LICENSE)。
