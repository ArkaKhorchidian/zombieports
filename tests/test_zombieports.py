import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from zombieports import cli, scan
from zombieports.scan import Listener


@pytest.mark.parametrize("cmd,name,expect", [
    ("node /app/node_modules/.bin/next dev -p 3000", "node", ("Next.js", "dev")),
    ("node /app/node_modules/.bin/vite", "node", ("Vite", "dev")),
    ("/usr/bin/python3 -m http.server 8000", "python3", ("python -m http.server", "dev")),
    ("uvicorn app:app --reload", "python3.12", ("Uvicorn", "dev")),
    ("python manage.py runserver", "python", ("Django", "dev")),
    ("redis-server *:6379", "redis-server", ("Redis", "db")),
    ("/usr/lib/postgresql/16/bin/postgres -D /var/lib/postgresql", "postgres", ("Postgres", "db")),
    ("node server.js", "node", ("Node", "dev")),
    ("/Applications/Docker.app/Contents/MacOS/com.docker.backend", "com.docker.backend", ("Docker", "tool")),
    ("ollama serve", "ollama", ("Ollama", "tool")),
    ("", "rapportd", ("rapportd", "other")),
])
def test_classify(cmd, name, expect):
    assert scan.classify(cmd, name) == expect


def test_find_project(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(json.dumps({"name": "@acme/storefront"}))
    (tmp_path / "web" / "src" / "pages").mkdir(parents=True)
    assert scan.find_project(str(tmp_path / "web" / "src" / "pages")) == ("@acme/storefront", str(tmp_path / "web"))
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "pyproject.toml").write_text("")
    assert scan.find_project(str(tmp_path / "api")) == ("api", str(tmp_path / "api"))
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "package.json").write_text("{nope")
    assert scan.find_project(str(tmp_path / "bad"))[0] == "bad"
    assert scan.find_project(None) == (None, None)


def L(port, **kw):
    base = dict(addresses=["127.0.0.1"], pid=1000 + port, name="node", category="dev", mine=True, kind="Vite",
                started=time.time() - 60, ppid=50, project="web", project_dir="/code/web", cwd=None)
    base.update(kw)
    return Listener(port, **base)


def test_mark_zombies(tmp_path):
    now = time.time()
    ls = [
        L(3000),
        L(3001, started=now - 3 * 86400),
        L(5173, ppid=1, project="other", project_dir="/code/other"),
        L(5174, started=now - 600, project="dup", project_dir="/code/dup"),
        L(5175, started=now - 60, project="dup", project_dir="/code/dup"),
        L(8000, cwd=str(tmp_path / "deleted"), project="gone", project_dir=str(tmp_path / "deleted")),
        L(6379, category="db", started=now - 30 * 86400, kind="Redis"),
        L(9000, ppid=1, project=None, project_dir=None, cwd="/"),  # an app the OS started
        L(7000, mine=False, started=now - 9 * 86400),
    ]
    scan.mark_zombies(ls, now=now)
    z = {l.port: l.zombie for l in ls}
    assert z[3000] is None and z[5175] is None and z[6379] is None and z[9000] is None and z[7000] is None
    assert z[3001].startswith("up for 3d") and "a newer copy is running" in z[3001]
    assert z[5174] == "a newer copy is running"
    assert z[8000] == "its folder was deleted"
    if sys.platform != "win32":
        assert z[5173] == "its terminal is gone"


def test_human():
    assert [scan.human_age(x) for x in (5, 300, 3 * 3600 + 120, 2 * 86400 + 5 * 3600, None)] == \
        ["5s", "5m", "3h 02m", "2d 5h", "?"]
    assert scan.human_bytes(512) == "512 B" and scan.human_bytes(45 * 2**20) == "45 MB" and scan.human_bytes(None) == ""


# ---------------------------------------------------------------------------
# Real processes
# ---------------------------------------------------------------------------
SERVER = """
import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen()
print(s.getsockname()[1], flush=True)
time.sleep(120)
"""


@pytest.fixture
def server(tmp_path):
    proj = tmp_path / "my-shop"
    proj.mkdir()
    (proj / "package.json").write_text(json.dumps({"name": "my-shop"}))
    p = subprocess.Popen([sys.executable, "-c", SERVER], cwd=proj, stdout=subprocess.PIPE, text=True)
    port = int(p.stdout.readline())
    yield p, port
    if p.poll() is None:
        p.kill()
    p.wait()


def test_scan_finds_real_server(server):
    p, port = server
    found = [l for l in scan.scan() if l.port == port]
    assert len(found) == 1
    l = found[0]
    assert l.pid == p.pid and l.mine and l.project == "my-shop" and l.kind == "Python" and l.category == "dev"
    assert l.addresses == ["127.0.0.1"] and not l.public and 0 <= l.uptime < 60 and l.zombie is None


def test_kill(server):
    p, port = server
    assert scan.kill(p.pid) in ("stopped", "killed (had to force it)")
    p.wait(timeout=10)
    assert scan.kill(p.pid) == "already gone"


def test_cli_list_json_and_kill(server, capsys):
    p, port = server
    assert cli.main(["--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["port"] == port and r["project"] == "my-shop" for r in rows)
    assert cli.main(["list"]) == 0
    assert "my-shop" in capsys.readouterr().out
    assert cli.main(["kill", str(port)]) == 0
    out = capsys.readouterr().out
    assert "stopped" in out or "killed" in out
    p.wait(timeout=10)
    assert cli.main(["kill", str(port)]) == 1


# ---------------------------------------------------------------------------
# The UI
# ---------------------------------------------------------------------------
def test_app():
    from zombieports.app import PortsApp
    state = {"rows": [L(3000), L(3001, zombie="up for 3d 1h"), L(5432, kind="Postgres", category="db", project=None),
                      L(631, pid=None, category="system", mine=False, kind="(another user or the system)")]}
    killed = []

    def scanner(include_system=True):
        return list(state["rows"])

    def killer(pid, force=False):
        killed.append((pid, force))
        state["rows"] = [r for r in state["rows"] if r.pid != pid]
        return "stopped"

    async def run():
        app = PortsApp(scanner=scanner, killer=killer, interval=60)
        async with app.run_test(size=(130, 30)) as pilot:
            t = app.query_one("DataTable")
            assert t.row_count == 3  # system rows hidden
            await pilot.press("a")
            assert t.row_count == 4
            await pilot.press("a")
            await pilot.press("k")  # kill the first row (:3000)
            assert killed == [(4000, False)] and t.row_count == 2
            await pilot.press("z")
            await pilot.press("y")
            await pilot.pause()
            assert (4001, False) in killed and t.row_count == 1
            await pilot.press("slash")
            await pilot.press(*"zzz")
            assert t.row_count == 0
            await pilot.press("escape")
            assert t.row_count == 1
            await pilot.press("q")

    asyncio.run(run())
