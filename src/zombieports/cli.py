"""zombieports: what's listening on your machine, which project started it, and one key to kill it."""
from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__, scan
from .app import CAT_STYLE


def table(listeners: list[scan.Listener]) -> Table:
    t = Table(box=None, header_style="dim", padding=(0, 2))
    for col in ("Port", "Project", "What", "Up for", "PID", "Memory"):
        t.add_column(col, no_wrap=True, overflow="ellipsis", max_width=28 if col in ("Project", "What") else None)
    t.add_column("", ratio=1, min_width=20)
    for l in listeners:
        status = Text()
        if l.zombie:
            status.append("zombie? ", "bold red")
            status.append(l.zombie, "red")
        elif l.public and l.category in ("dev", "db"):
            status.append("open to your network", "yellow")
        t.add_row(Text(str(l.port), "bold"), Text(l.project or "—", "bold" if l.project else "bright_black"),
                  Text(l.kind, CAT_STYLE.get(l.category, "")), scan.human_age(l.uptime) if l.started else "",
                  str(l.pid or ""), scan.human_bytes(l.rss), status)
    return t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zombieports", description="See every port listening on your machine, which "
                                 "project started it and how long it's been running. Kill zombies with one key.")
    ap.add_argument("command", nargs="?", choices=["list", "kill"], help="list (no UI) or kill PORT...; default: the UI")
    ap.add_argument("ports", nargs="*", type=int, help="ports to kill")
    ap.add_argument("--zombies", action="store_true", help="with kill: kill every server that looks abandoned")
    ap.add_argument("-f", "--force", action="store_true", help="with kill: SIGKILL straight away")
    ap.add_argument("-a", "--all", action="store_true", help="include other users' and system processes")
    ap.add_argument("--json", action="store_true", help="print the list as JSON")
    ap.add_argument("-V", "--version", action="version", version=f"zombieports {__version__}")
    a = ap.parse_args(argv)
    console = Console()
    if not console.is_terminal:
        console = Console(width=160)

    if a.command == "kill" or (a.command is None and a.ports):
        listeners = scan.scan(include_system=True)
        targets = [l for l in listeners if l.port in a.ports]
        if a.zombies:
            targets += [l for l in listeners if l.zombie and l not in targets]
        missing = set(a.ports) - {l.port for l in targets}
        for p in sorted(missing):
            console.print(f":{p}  [dim]nothing listening[/]")
        if not targets and not missing:
            console.print("Nothing to kill." if a.zombies else "Give me some ports, e.g. [bold]zombieports kill 3000[/]")
        done = set()
        for l in targets:
            if l.pid in done:
                continue
            if not l.pid:
                console.print(f":{l.port}  [yellow]belongs to another user or the system; try with sudo[/]")
                continue
            result = scan.kill(l.pid, force=a.force)
            done.add(l.pid)
            console.print(f":{l.port}  {l.project or l.kind}  [{'green' if 'stop' in result or 'kill' in result else 'yellow'}]"
                          f"{result}[/]")
        return 0 if targets and not missing else 1

    listeners = scan.scan(include_system=a.all)
    if a.json:
        print(json.dumps([l.dict() for l in listeners], indent=2))
        return 0
    if a.command == "list" or not console.is_terminal:
        if not listeners:
            console.print("Nothing is listening.")
            return 0
        console.print(table(listeners))
        z = [l for l in listeners if l.zombie]
        if z:
            console.print(f"\n[bold red]{len(z)} look{'s' * (len(z) == 1)} abandoned.[/] Kill {'it' if len(z) == 1 else 'them'} with [bold]zombieports kill --zombies[/]")
        return 0
    from .app import PortsApp
    PortsApp(show_all=a.all).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
