#!/usr/bin/env python3
"""看板命令行。所有 AI 都用它读写看板，只依赖 Python 标准库。

需要三个环境变量（或写在 ~/.config/ai-board.env，每行 KEY=VALUE，权限 600，不进任何仓库）：
  BOARD_URL    例如 http://100.x.x.x:8765
  BOARD_TOKEN  访问口令
  BOARD_ACTOR  我是谁，例如 Claude Code / Gemini CLI / Codex / 人的名字

用法：
  board.py list [all|待做|进行中|卡住|完成|放弃]
  board.py show 编号                     看一件事和它的全部流水
  board.py add "标题" ["说明"]
  board.py claim 编号                    认领，别人认领过会被拒绝
  board.py log 编号 "做了什么" ["产物路径"]
  board.py note "做了什么" ["产物路径"]    不属于任何任务的记录
  board.py done 编号 "结果" ["产物路径"]
  board.py stuck 编号 "卡在哪"
  board.py drop 编号 "为什么放弃"
  board.py release 编号 ["原因"]          自己放手；加 --force 抢回别人的（必须写原因）
  board.py recent [条数]                 最近的流水
  board.py export [输出文件]              导出 markdown 镜像
"""
import json
import os
import sys
import urllib.error
import urllib.request


def load_env():
    path = os.path.expanduser("~/.config/ai-board.env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    miss = [k for k in ("BOARD_URL", "BOARD_TOKEN", "BOARD_ACTOR") if not os.environ.get(k)]
    if miss:
        sys.exit("缺少环境变量：" + "、".join(miss) + "（见 board.py 开头说明）")


def call(method, path, body=None, raw=False):
    url = os.environ["BOARD_URL"].rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + os.environ["BOARD_TOKEN"])
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            txt = r.read().decode()
            return txt if raw else json.loads(txt)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", "")
        except Exception:
            msg = ""
        sys.exit(f"失败（{e.code}）：{msg}")
    except urllib.error.URLError as e:
        sys.exit(f"连不上看板服务：{e.reason}。先读最近一次导出的看板镜像，别停工，连上后补记流水。")


def show_task(t):
    who = f"｜{t['owner']}" if t["owner"] else ""
    flag = "｜⚠超过2小时没动静" if t.get("stale") else ""
    print(f"#{t['id']} [{t['status']}] {t['title']}{who}{flag}")
    if t.get("note"):
        print(f"    {t['note']}")


def show_events(evs):
    for e in evs:
        tid = f"#{e['task_id']} " if e["task_id"] else ""
        art = f"（产物：{e['artifact']}）" if e["artifact"] else ""
        print(f"{e['ts']}｜{e['actor']}｜{tid}{e['action']}｜{e['detail']}{art}")


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    load_env()
    me = os.environ["BOARD_ACTOR"]
    cmd, rest = a[0], a[1:]
    arg = lambda i, d=None: rest[i] if len(rest) > i else d

    if cmd == "list":
        st = arg(0)
        r = call("GET", "/tasks" + (f"?status={urllib.request.quote(st)}" if st else ""))
        if not r["tasks"]:
            print("（没有）")
        for t in r["tasks"]:
            show_task(t)
    elif cmd == "show":
        show_task(call("GET", f"/tasks/{arg(0)}")["task"])
        show_events(call("GET", f"/events?task_id={arg(0)}&limit=500")["events"])
    elif cmd == "add":
        show_task(call("POST", "/tasks", {"actor": me, "title": arg(0, ""), "note": arg(1, "")})["task"])
    elif cmd == "claim":
        show_task(call("POST", f"/tasks/{arg(0)}/claim", {"actor": me})["task"])
    elif cmd == "log":
        r = call("POST", "/events", {"actor": me, "task_id": arg(0), "detail": arg(1, ""), "artifact": arg(2, "")})
        print("已记", r["ts"])
    elif cmd == "note":
        r = call("POST", "/events", {"actor": me, "detail": arg(0, ""), "artifact": arg(1, "")})
        print("已记", r["ts"])
    elif cmd in ("done", "stuck", "drop"):
        st = {"done": "完成", "stuck": "卡住", "drop": "放弃"}[cmd]
        show_task(call("POST", f"/tasks/{arg(0)}/status",
                       {"actor": me, "status": st, "detail": arg(1, ""), "artifact": arg(2, "")})["task"])
    elif cmd == "release":
        force = "--force" in rest
        rest = [x for x in rest if x != "--force"]
        show_task(call("POST", f"/tasks/{arg(0)}/release",
                       {"actor": me, "detail": arg(1, ""), "force": force})["task"])
    elif cmd == "recent":
        show_events(call("GET", f"/events?limit={arg(0, '30')}")["events"])
    elif cmd == "export":
        md = call("GET", "/export.md", raw=True)
        out = arg(0)
        if out:
            open(out, "w", encoding="utf-8").write(md)
            print("已导出到", out)
        else:
            print(md)
    else:
        sys.exit(f"不认识的命令：{cmd}，用 board.py help 看用法")


if __name__ == "__main__":
    main(sys.argv[1:])
