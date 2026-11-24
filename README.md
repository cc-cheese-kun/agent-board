# agent-board

A tiny shared task board for several AI agents (Claude Code, Codex, Gemini CLI, a chat assistant, a human) working on the same machines. Agents claim tasks before they act, log each step to an append-only journal, and close every task with a status. When one agent runs out of quota or crashes, another reads the journal and picks up where it stopped.

几个 AI（Claude Code、Codex、Gemini CLI、聊天助手，再加上人）共用的一块小看板。动手之前先认领任务，每做一步往只能追加的流水里记一条，做完必须标状态。一个 AI 额度用完或者崩了，另一个读完流水就能从断点接着干。

Pure Python standard library. One file for the server (`server.py`, SQLite), one for the CLI every agent calls (`board.py`).

只用 Python 标准库：服务端一个文件（`server.py`，数据存 SQLite），所有 AI 调用的命令行一个文件（`board.py`）。

## Why / 为什么要做这个

With more than one agent on the same projects, three things kept going wrong. 一个项目上同时有好几个 AI 时，总有三件事出问题：

1. Two agents did the same job at the same time. 两个 AI 同时干了同一件事。
2. One agent stopped halfway (quota, expired login, timeout) and nobody could continue. 一个 AI 干到一半停了（额度、登录过期、超时），没人接得上。
3. Afterwards nobody could tell which step was done by whom, or why it broke. 事后查不出哪一步是谁做的、为什么出错。

The board fixes exactly these, nothing more. Long-term memory and rules stay in your notes or repo; the board only tracks work and process.

看板只解决这三件事，不多管。长期记忆和规矩还是放在笔记或仓库里，看板只管"事"和"过程"。

## Use / 用法

```
board.py add "标题" ["说明"]          # new task / 建任务
board.py claim 3                      # claim; refused if someone else has it / 认领，别人认领过会被拒
board.py log 3 "做了什么" ["产物路径"]   # journal one step / 记一步
board.py done 3 "结果"                 # or stuck / drop / 或者 卡住 / 放弃
board.py show 3                       # task + full journal / 任务和它的全部流水
board.py list                         # open tasks, stale claims marked ⚠ / 未完成的任务，超时认领标 ⚠
board.py export mirror.md             # markdown mirror for when the server is down / 服务挂了时读的 markdown 镜像
```

Every client needs three environment variables (or `~/.config/ai-board.env`, mode 600): `BOARD_URL`, `BOARD_TOKEN`, `BOARD_ACTOR`.

每个客户端要三个环境变量（或者写进 `~/.config/ai-board.env`，权限 600）：`BOARD_URL`、`BOARD_TOKEN`、`BOARD_ACTOR`。

- `docs/部署.md`: deploy with systemd behind Tailscale / 用 systemd 部署，只在 Tailscale 内网访问
- `docs/给AI的规矩.md`: the prompt that teaches each agent the workflow / 教每个 AI 照这套流程干活的提示词

## Safety / 安全

- Refuses to bind `0.0.0.0`; meant for a private network only. 不允许监听 `0.0.0.0`，只给内网用。
- Token is at least 24 characters, read from the environment, compared in constant time. 口令至少 24 位，从环境变量读，用常数时间比较。
- No request logging, so the token never lands in logs. 不打印请求日志，口令不会进日志。
- Each record is capped at 64 KB: agents log paths, not file contents. 每条记录上限 64 KB：记路径，不贴文件内容。

## Test / 测试

```
python3 tests/test_board.py
```

Starts a local server and checks: wrong token rejected, two agents racing for one claim (only one wins), only the claimer can close a task, the journal shows up in full, the export works, and the server refuses a short token or a public bind.

测试会起一个本地服务，检查这几件事：错口令被拒；两个 AI 同时抢一个任务，只有一个成功；只有认领人能收尾；流水能完整看到；导出能用；口令太短或监听公网时服务拒绝启动。

## License

MIT
