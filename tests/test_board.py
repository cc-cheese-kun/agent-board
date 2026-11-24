"""End-to-end test: start a local server and drive it through board.py - create a
task, race two agents for it (only one may win), log events, close it, export.
Also checks a wrong token is rejected, a short token and 0.0.0.0 are refused.

Usage: python3 tests/test_board.py

端到端测试：起一个本地服务，用 board.py 走一遍建任务、抢认领、记流水、收尾、导出。

python3 tests/test_board.py
"""
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failed = 0


def check(ok, what):
    global failed
    print(('  ok   ' if ok else '  FAIL ') + what)
    failed += 0 if ok else 1


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main():
    tmp = tempfile.mkdtemp()
    port = free_port()
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, BOARD_TOKEN=token, BOARD_HOST='127.0.0.1', BOARD_PORT=str(port),
               BOARD_DB=os.path.join(tmp, 'board.db'), HOME=tmp)
    server = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        def board(actor, *args, token_=token):
            e = dict(env, BOARD_URL=f'http://127.0.0.1:{port}', BOARD_ACTOR=actor, BOARD_TOKEN=token_)
            return subprocess.run([sys.executable, os.path.join(ROOT, 'board.py'), *args],
                                  env=e, capture_output=True, text=True)

        r = board('alice', 'list', token_='wrong-token-wrong-token-xx')
        check(r.returncode != 0, '口令不对被拒')

        r = board('alice', 'add', '测试任务', '说明')
        check(r.returncode == 0, 'add 成功')
        r = board('alice', 'list')
        check('测试任务' in r.stdout, 'list 看得到新任务')

        results = {}

        def grab(name):
            results[name] = board(name, 'claim', '1').returncode

        threads = [threading.Thread(target=grab, args=(n,)) for n in ('alice', 'bob')]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check(sorted(results.values()) == [0, 1] or list(results.values()).count(0) == 1,
              f'两个人同时认领，只有一个成功（{results}）')

        winner = [n for n, c in results.items() if c == 0][0]
        loser = 'bob' if winner == 'alice' else 'alice'
        check(board(winner, 'log', '1', '做了第一步', 'out/a.txt').returncode == 0, '认领人能记流水')
        check(board(loser, 'done', '1', '抢着收尾').returncode != 0, '别人认领的任务不能改状态')
        check(board(winner, 'done', '1', '完成').returncode == 0, '认领人能收尾')
        r = board(winner, 'show', '1')
        check('做了第一步' in r.stdout and '完成' in r.stdout, 'show 能看到全部流水')

        out = os.path.join(tmp, 'mirror.md')
        board(winner, 'export', out)
        check(os.path.exists(out) and '测试任务' in open(out, encoding='utf-8').read(), 'export 导出了 markdown 镜像')
    finally:
        server.terminate()
        server.wait()

    bad = subprocess.run([sys.executable, os.path.join(ROOT, 'server.py')],
                         env=dict(os.environ, BOARD_TOKEN='short', BOARD_DB=os.path.join(tmp, 'x.db')),
                         capture_output=True, text=True, timeout=10)
    check(bad.returncode != 0, '口令太短拒绝启动')
    pub = subprocess.run([sys.executable, os.path.join(ROOT, 'server.py')],
                         env=dict(os.environ, BOARD_TOKEN=token, BOARD_HOST='0.0.0.0',
                                  BOARD_DB=os.path.join(tmp, 'y.db')),
                         capture_output=True, text=True, timeout=10)
    check(pub.returncode != 0, '监听 0.0.0.0 拒绝启动')

    print('\n全部通过' if not failed else f'\n{failed} 项没过')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
