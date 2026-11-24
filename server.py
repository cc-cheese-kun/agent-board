#!/usr/bin/env python3
"""AI 共用看板服务：任务看板 + 只追加的流水。只用 Python 标准库。

环境变量：
  BOARD_TOKEN  必填，访问口令（只放环境变量，不进仓库）
  BOARD_HOST   监听地址，默认 127.0.0.1（部署时填 Tailscale 内网 IP，不要填 0.0.0.0）
  BOARD_PORT   端口，默认 8765
  BOARD_DB     数据库路径，默认 ./board.db
"""
import hmac
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = os.environ.get("BOARD_TOKEN", "")
HOST = os.environ.get("BOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("BOARD_PORT", "8765"))
DB = os.environ.get("BOARD_DB", "board.db")
TZ = timezone(timedelta(hours=8))  # 北京时间
STATUSES = ["待做", "进行中", "卡住", "完成", "放弃"]
STALE_HOURS = 2  # 进行中超过这么久没有新流水，列表里标「可能没人管了」

LOCK = threading.Lock()


def now():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def connect():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT '待做',
              owner TEXT,
              note TEXT DEFAULT '',
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_event_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT NOT NULL,
              actor TEXT NOT NULL,
              task_id INTEGER,
              action TEXT NOT NULL,
              detail TEXT DEFAULT '',
              artifact TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ev_task ON events(task_id);
            """
        )


def add_event(c, actor, task_id, action, detail="", artifact=""):
    ts = now()
    c.execute(
        "INSERT INTO events(ts,actor,task_id,action,detail,artifact) VALUES(?,?,?,?,?,?)",
        (ts, actor, task_id, action, detail, artifact),
    )
    if task_id is not None:
        c.execute("UPDATE tasks SET last_event_at=? WHERE id=?", (ts, task_id))
    return ts


def task_row(r):
    d = dict(r)
    d["stale"] = False
    if d["status"] == "进行中":
        last = datetime.strptime(d["last_event_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        d["stale"] = datetime.now(TZ) - last > timedelta(hours=STALE_HOURS)
    return d


class Err(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


def need(body, *keys):
    for k in keys:
        v = body.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise Err(400, f"缺少字段：{k}")


def get_task(c, tid):
    r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        raise Err(404, f"没有 {tid} 号任务")
    return r


# ---------- 各个动作 ----------

def list_tasks(q):
    status = q.get("status", [None])[0]
    with connect() as c:
        if status == "all":
            rows = c.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        elif status:
            rows = c.execute("SELECT * FROM tasks WHERE status=? ORDER BY id", (status,)).fetchall()
        else:  # 默认只列没结束的
            rows = c.execute(
                "SELECT * FROM tasks WHERE status IN ('待做','进行中','卡住') ORDER BY id"
            ).fetchall()
    return {"tasks": [task_row(r) for r in rows]}


def create_task(body):
    need(body, "actor", "title")
    with LOCK, connect() as c:
        ts = now()
        cur = c.execute(
            "INSERT INTO tasks(title,note,created_by,created_at,updated_at,last_event_at) VALUES(?,?,?,?,?,?)",
            (body["title"].strip(), body.get("note", ""), body["actor"], ts, ts, ts),
        )
        tid = cur.lastrowid
        add_event(c, body["actor"], tid, "新建", body["title"].strip())
        return {"task": task_row(get_task(c, tid))}


def claim(tid, body):
    need(body, "actor")
    actor = body["actor"]
    with LOCK, connect() as c:
        t = get_task(c, tid)
        if t["status"] in ("完成", "放弃"):
            raise Err(409, f"{tid} 号已经{t['status']}，不能认领")
        if t["owner"] and t["owner"] != actor:
            raise Err(409, f"{tid} 号已被 {t['owner']} 认领，别动")
        c.execute(
            "UPDATE tasks SET owner=?, status='进行中', updated_at=? WHERE id=?",
            (actor, now(), tid),
        )
        add_event(c, actor, tid, "认领", body.get("detail", ""))
        return {"task": task_row(get_task(c, tid))}


def release(tid, body):
    need(body, "actor")
    with LOCK, connect() as c:
        t = get_task(c, tid)
        if t["owner"] != body["actor"] and not body.get("force"):
            raise Err(409, f"{tid} 号是 {t['owner']} 的，不是你的；确实要抢回来请加 force 并写明原因")
        if body.get("force"):
            need(body, "detail")
        c.execute("UPDATE tasks SET owner=NULL, status='待做', updated_at=? WHERE id=?", (now(), tid))
        add_event(c, body["actor"], tid, "强制放手" if body.get("force") else "放手", body.get("detail", ""))
        return {"task": task_row(get_task(c, tid))}


def set_status(tid, body):
    need(body, "actor", "status")
    st = body["status"]
    if st not in STATUSES:
        raise Err(400, f"状态只能是：{'、'.join(STATUSES)}")
    with LOCK, connect() as c:
        t = get_task(c, tid)
        if t["owner"] != body["actor"]:
            who = t["owner"] or "没人"
            raise Err(409, f"{tid} 号现在归{who}，先认领再改状态")
        if st in ("完成", "放弃", "卡住"):
            need(body, "detail")  # 结束或卡住都要写清楚
        note = body.get("detail", "")
        c.execute("UPDATE tasks SET status=?, updated_at=?, note=? WHERE id=?",
                  (st, now(), note or t["note"], tid))
        add_event(c, body["actor"], tid, f"状态→{st}", note, body.get("artifact", ""))
        return {"task": task_row(get_task(c, tid))}


def log_event(body):
    need(body, "actor", "detail")
    tid = body.get("task_id")
    with LOCK, connect() as c:
        if tid is not None:
            tid = int(tid)
            get_task(c, tid)
        ts = add_event(c, body["actor"], tid, body.get("action", "记录"), body["detail"], body.get("artifact", ""))
        return {"ok": True, "ts": ts}


def list_events(q):
    tid = q.get("task_id", [None])[0]
    limit = min(int(q.get("limit", ["50"])[0]), 500)
    with connect() as c:
        if tid:
            rows = c.execute("SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (int(tid), limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"events": [dict(r) for r in reversed(rows)]}


def export_md():
    with connect() as c:
        tasks = [task_row(r) for r in c.execute("SELECT * FROM tasks ORDER BY id").fetchall()]
        evs = c.execute("SELECT * FROM events ORDER BY id").fetchall()
    out = [f"# AI 共用看板（镜像，正本在 VPS，导出于 {now()}）", "", "> 这是只读镜像，改动请走看板服务，不要改这个文件。", ""]
    for st in STATUSES:
        group = [t for t in tasks if t["status"] == st]
        if not group:
            continue
        out.append(f"## {st}（{len(group)}）")
        for t in group:
            who = f"｜{t['owner']}" if t["owner"] else ""
            flag = "｜⚠ 超过 2 小时没动静" if t["stale"] else ""
            out.append(f"- #{t['id']} {t['title']}{who}{flag}")
            if t["note"]:
                out.append(f"  - {t['note']}")
        out.append("")
    out.append("## 流水")
    by_task = {}
    for e in evs:
        by_task.setdefault(e["task_id"], []).append(e)
    for t in tasks:
        out.append(f"### #{t['id']} {t['title']}")
        for e in by_task.get(t["id"], []):
            art = f"（产物：{e['artifact']}）" if e["artifact"] else ""
            out.append(f"- {e['ts']}｜{e['actor']}｜{e['action']}｜{e['detail']}{art}")
        out.append("")
    if by_task.get(None):
        out.append("### 不属于任何任务的记录")
        for e in by_task[None]:
            art = f"（产物：{e['artifact']}）" if e["artifact"] else ""
            out.append(f"- {e['ts']}｜{e['actor']}｜{e['action']}｜{e['detail']}{art}")
    return "\n".join(out) + "\n"


# ---------- HTTP ----------

class H(BaseHTTPRequestHandler):
    server_version = "ai-board/1"

    def log_message(self, fmt, *a):  # 不打印请求，免得口令进日志
        pass

    def _send(self, code, obj, ctype="application/json; charset=utf-8"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        got = self.headers.get("Authorization", "")
        return bool(TOKEN) and hmac.compare_digest(got, f"Bearer {TOKEN}")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise Err(413, "内容太长（上限 64KB），产物请写路径，不要贴全文")
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            raise Err(400, "请求体不是合法 JSON")

    def _route(self, method):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        if parts == ["health"]:
            return self._send(200, {"ok": True})
        if not self._auth():
            return self._send(401, {"error": "口令不对"})
        try:
            if method == "GET" and parts == ["tasks"]:
                return self._send(200, list_tasks(q))
            if method == "POST" and parts == ["tasks"]:
                return self._send(200, create_task(self._body()))
            if method == "GET" and len(parts) == 2 and parts[0] == "tasks":
                with connect() as c:
                    return self._send(200, {"task": task_row(get_task(c, int(parts[1])))})
            if method == "POST" and len(parts) == 3 and parts[0] == "tasks":
                tid, act = int(parts[1]), parts[2]
                fn = {"claim": claim, "release": release, "status": set_status}.get(act)
                if fn:
                    return self._send(200, fn(tid, self._body()))
            if method == "GET" and parts == ["events"]:
                return self._send(200, list_events(q))
            if method == "POST" and parts == ["events"]:
                return self._send(200, log_event(self._body()))
            if method == "GET" and parts == ["export.md"]:
                return self._send(200, export_md().encode(), "text/markdown; charset=utf-8")
            raise Err(404, "没有这个地址")
        except Err as e:
            return self._send(e.code, {"error": e.msg})
        except ValueError:
            return self._send(400, {"error": "编号必须是数字"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


def main():
    if len(TOKEN) < 24:
        sys.exit("BOARD_TOKEN 没设或太短（至少 24 位），拒绝启动")
    if HOST == "0.0.0.0" and os.environ.get("BOARD_ALLOW_PUBLIC") != "1":
        sys.exit("不允许监听 0.0.0.0（会暴露到公网）。请填 Tailscale 内网 IP 或 127.0.0.1")
    init_db()
    print(f"看板服务启动：{HOST}:{PORT}，数据库 {DB}", flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()


if __name__ == "__main__":
    main()
