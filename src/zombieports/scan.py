"""Everything listening on this machine: which project started it, what it is, and whether it looks abandoned."""
from __future__ import annotations

import getpass
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

DAY = 86400
MARKERS = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "Gemfile", "composer.json", "mix.exs",
           "deno.json", "setup.py", "manage.py", ".git")

# (pattern over the command line, label, category)
KINDS = [
    (r"\bnext(\.js)?\b.*\b(dev|start)\b|next-server|\.next/", "Next.js", "dev"),
    (r"\bvite\b", "Vite", "dev"),
    (r"\bnuxi?\b", "Nuxt", "dev"),
    (r"\bastro\b", "Astro", "dev"),
    (r"\bremix\b", "Remix", "dev"),
    (r"\bsvelte-kit\b", "SvelteKit", "dev"),
    (r"\bstorybook\b", "Storybook", "dev"),
    (r"react-scripts|webpack(-dev-server)?\b|\brspack\b|\bparcel\b", "Webpack", "dev"),
    (r"\bng serve\b|@angular/cli", "Angular", "dev"),
    (r"\bexpo\b", "Expo", "dev"),
    (r"\bwrangler\b", "Wrangler", "dev"),
    (r"\bnodemon\b|\btsx\b.*watch|ts-node-dev", "Node (watch)", "dev"),
    (r"\buvicorn\b", "Uvicorn", "dev"),
    (r"\bgunicorn\b", "Gunicorn", "dev"),
    (r"\bhypercorn\b|\bgranian\b", "ASGI server", "dev"),
    (r"\bfastapi\b", "FastAPI", "dev"),
    (r"\bflask\b", "Flask", "dev"),
    (r"manage\.py\s+runserver|\bdjango\b", "Django", "dev"),
    (r"\bstreamlit\b", "Streamlit", "dev"),
    (r"\bgradio\b", "Gradio", "dev"),
    (r"\bjupyter|ipykernel|jupyterlab", "Jupyter", "dev"),
    (r"\bhttp\.server\b|SimpleHTTPServer", "python -m http.server", "dev"),
    (r"\brails\b|\bpuma\b|\bunicorn\b", "Rails", "dev"),
    (r"\bphp\b.*-S\b|\bartisan serve\b", "PHP", "dev"),
    (r"\bhugo\b", "Hugo", "dev"),
    (r"\bjekyll\b", "Jekyll", "dev"),
    (r"\bmkdocs\b", "MkDocs", "dev"),
    (r"\bdocusaurus\b", "Docusaurus", "dev"),
    (r"\bcargo\b.*\brun\b|\btrunk\b", "Cargo", "dev"),
    (r"\bgo-build\b|/go-build|\bgo run\b|\bair\b", "Go", "dev"),
    (r"\bdotnet\b", ".NET", "dev"),
    (r"\bspring|\bgradle|\bmvn\b|\bjava\b", "Java", "dev"),
    (r"\bpostgres\b|\bpostmaster\b", "Postgres", "db"),
    (r"\bmysqld\b|\bmariadbd?\b", "MySQL", "db"),
    (r"\bredis-server\b|\bvalkey-server\b", "Redis", "db"),
    (r"\bmongod\b", "MongoDB", "db"),
    (r"\belasticsearch\b|\bopensearch\b", "Elasticsearch", "db"),
    (r"\bmemcached\b", "Memcached", "db"),
    (r"\bclickhouse\b", "ClickHouse", "db"),
    (r"\bminio\b", "MinIO", "db"),
    (r"ControlCenter|AirPlayXPCHelper", "AirPlay Receiver", "tool"),  # why port 5000/7000 is taken on a Mac
    (r"\bollama\b", "Ollama", "tool"),
    (r"\blm[- ]?studio\b", "LM Studio", "tool"),
    (r"docker-proxy|com\.docker|vpnkit|Docker Desktop|\bdockerd\b", "Docker", "tool"),
    (r"\borbstack\b", "OrbStack", "tool"),
    (r"\bcode\b.*(helper|server)|Code Helper|vscode-server", "VS Code", "tool"),
    (r"\bcursor\b", "Cursor", "tool"),
    (r"\bngrok\b|\bcloudflared\b", "Tunnel", "tool"),
    (r"\bnode\b", "Node", "dev"),
    (r"\bpython[\d.]*\b|\bpython\.exe\b", "Python", "dev"),
    (r"\bruby\b", "Ruby", "dev"),
    (r"\bdeno\b", "Deno", "dev"),
    (r"\bbun\b", "Bun", "dev"),
]
_KINDS = [(re.compile(p, re.I), label, cat) for p, label, cat in KINDS]
GENERIC = {"Node", "Python", "Ruby", "Deno", "Bun"}


@dataclass
class Listener:
    port: int
    addresses: list[str]
    pid: int | None
    name: str = ""
    cmdline: list[str] = field(default_factory=list)
    cwd: str | None = None
    started: float | None = None
    rss: int | None = None
    user: str | None = None
    ppid: int | None = None
    project: str | None = None
    project_dir: str | None = None
    kind: str = ""
    category: str = "other"  # dev, db, tool, system, other
    mine: bool = False
    zombie: str | None = None  # why it looks abandoned

    @property
    def uptime(self) -> float | None:
        return time.time() - self.started if self.started else None

    @property
    def public(self) -> bool:
        return any(a in ("0.0.0.0", "::", "*") for a in self.addresses)

    @property
    def command(self) -> str:
        return " ".join(self.cmdline) if self.cmdline else self.name

    def dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("port", "addresses", "pid", "name", "cwd", "started", "rss", "user",
                                            "project", "project_dir", "kind", "category", "mine", "zombie")}
        d["command"] = self.command
        d["uptime"] = self.uptime
        return d


def classify(cmdline: str, name: str) -> tuple[str, str]:
    text = f"{name} {cmdline}"
    for rx, label, cat in _KINDS:
        if rx.search(text):
            return label, cat
    return (name or "?"), "other"


def find_project(path: str | None) -> tuple[str | None, str | None]:
    """(project name, project root) for a working directory: package.json's name, else the folder name."""
    if not path:
        return None, None
    p = Path(path)
    home = Path.home()
    for d in [p, *p.parents]:
        if d == home or d == d.parent:
            break
        if any((d / m).exists() for m in MARKERS):
            pj = d / "package.json"
            if pj.is_file():
                try:
                    name = json.loads(pj.read_text(encoding="utf-8", errors="replace")).get("name")
                    if isinstance(name, str) and name:
                        return name, str(d)
                except (ValueError, OSError):
                    pass
            return d.name, str(d)
    # no project markers: name it after the folder if it's yours (or was, if it's been deleted)
    try:
        inside_home = p.resolve().is_relative_to(home.resolve()) if hasattr(p, "is_relative_to") else \
            str(p.resolve()).startswith(str(home.resolve()))
    except OSError:
        inside_home = False
    if p != home and p.parent != p and (inside_home or not p.exists()):
        return p.name, str(p)
    return None, None


def _listening() -> dict[tuple[int | None, int], set[str]]:
    """{(pid, port): addresses} for every TCP socket in LISTEN state we can see."""
    out: dict[tuple[int | None, int], set[str]] = {}

    def add(pid, c):
        if c.status == psutil.CONN_LISTEN and c.laddr:
            out.setdefault((pid, c.laddr.port), set()).add(c.laddr.ip.split("%")[0])

    try:
        if sys.platform == "darwin":
            raise psutil.AccessDenied()  # macOS needs root for the system-wide call; per-process works for our own
        for c in psutil.net_connections(kind="tcp"):
            add(c.pid, c)
    except psutil.AccessDenied:
        for p in psutil.process_iter():
            try:
                conns = (getattr(p, "net_connections", None) or p.connections)(kind="tcp")
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                continue
            for c in conns:
                add(p.pid, c)
    return out


def scan(include_system: bool = True) -> list[Listener]:
    me = getpass.getuser()
    found: dict[tuple[int | None, int], Listener] = {}
    for (pid, port), addrs in _listening().items():
        found[(pid, port)] = Listener(port, sorted(addrs), pid)
    for l in found.values():
        if not l.pid:
            l.category, l.kind = "system", "(another user or the system)"
            continue
        try:
            p = psutil.Process(l.pid)
            with p.oneshot():
                l.name = p.name()
                l.started = p.create_time()
                l.ppid = p.ppid()
                try:
                    l.user = p.username()
                except (psutil.AccessDenied, KeyError):
                    l.user = None
                try:
                    l.cmdline = p.cmdline()
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    l.cmdline = []
                try:
                    l.cwd = p.cwd()
                except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    l.cwd = None
                try:
                    l.rss = p.memory_info().rss
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            l.category = "system"
            continue
        l.mine = bool(l.user) and l.user.split("\\")[-1] == me.split("\\")[-1]
        l.kind, l.category = classify(" ".join(l.cmdline), l.name)
        if l.kind in GENERIC and l.ppid:  # e.g. uvicorn --reload serves from a multiprocessing child
            try:
                parent = psutil.Process(l.ppid)
                kind, cat = classify(" ".join(parent.cmdline()), parent.name())
                if kind not in GENERIC and cat == l.category:
                    l.kind = kind
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        if not l.mine:
            l.category = "system" if l.category in ("other", "dev") else l.category
        l.project, l.project_dir = find_project(l.cwd) if l.mine else (None, None)
    listeners = list(found.values())
    mark_zombies(listeners)
    if not include_system:
        listeners = [l for l in listeners if l.category != "system"]
    return sorted(listeners, key=lambda l: (l.category == "system", l.port))


def mark_zombies(listeners: list[Listener], now: float | None = None, stale_hours: float = 24) -> None:
    """A dev server is probably forgotten if it's been up for a day, its folder is gone, its terminal is gone,
    or a newer copy of the same project is running."""
    now = now or time.time()
    newest: dict[tuple[str, str], float] = {}
    for l in listeners:
        if l.category == "dev" and l.project_dir and l.started:
            k = (l.project_dir, l.kind)
            newest[k] = max(newest.get(k, 0), l.started)
    for l in listeners:
        if l.category != "dev" or not l.mine:
            continue
        reasons = []
        if l.cwd and not os.path.isdir(l.cwd):
            reasons.append("its folder was deleted")
        in_project = bool(l.project_dir)  # dev servers run from a project; apps launched by the OS run from "/"
        if in_project and l.started and now - l.started > stale_hours * 3600:
            reasons.append(f"up for {human_age(now - l.started)}")
        if in_project and l.ppid == 1 and sys.platform != "win32":
            reasons.append("its terminal is gone")
        if l.project_dir and l.started and newest.get((l.project_dir, l.kind), 0) > l.started + 1:
            reasons.append("a newer copy is running")
        l.zombie = ", ".join(reasons) or None


def human_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < DAY:
        h, m = divmod(int(seconds // 60), 60)
        return f"{h}h {m:02d}m"
    d, h = divmod(int(seconds // 3600), 24)
    return f"{d}d {h}h"


def human_bytes(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}" if n < 10 else f"{n:.0f} {unit}"
        n /= 1024
    return ""


def kill(pid: int, grace: float = 3.0, force: bool = False) -> str:
    """Stop a process and its children: SIGTERM, then SIGKILL if it's still around after `grace` seconds."""
    try:
        p = psutil.Process(pid)
        procs = p.children(recursive=True) + [p]
    except psutil.NoSuchProcess:
        return "already gone"
    except psutil.AccessDenied:
        return "not allowed (owned by another user)"
    for q in procs:
        try:
            q.kill() if force else q.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(procs, timeout=grace)
    if alive and not force:
        for q in alive:
            try:
                q.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _, alive = psutil.wait_procs(alive, timeout=grace)
        if not alive:
            return "killed (had to force it)"
    return "stopped" if not alive else "still running (not allowed?)"


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0
