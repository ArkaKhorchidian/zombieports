"""The interactive view: every port, which project owns it, and one key to kill it."""
from __future__ import annotations

import webbrowser

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static

from . import scan
from .scan import Listener, human_age, human_bytes

CAT_STYLE = {"dev": "bright_cyan", "db": "magenta", "tool": "blue", "system": "bright_black", "other": "white"}


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]
    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Vertical { width: 64; height: auto; border: round $error; padding: 1 2; background: $surface; }
    """

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.message)
            yield Static(Text("\n[y] yes   [n] no", style="dim"))

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class PortsApp(App):
    TITLE = "zombieports"
    CSS = """
    Screen { background: $background; }
    #head { height: 1; padding: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    DataTable { height: 1fr; scrollbar-size-horizontal: 0; }
    #filter { display: none; }
    #filter.on { display: block; }
    """
    BINDINGS = [
        Binding("k,delete", "kill", "Kill"),
        Binding("K", "force_kill", "Force kill", show=False),
        Binding("z", "kill_zombies", "Kill zombies"),
        Binding("o", "open", "Open in browser"),
        Binding("a", "toggle_all", "All/mine"),
        Binding("slash", "filter", "Filter"),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, show_all: bool = False, scanner=scan.scan, killer=scan.kill, interval: float = 2.0):
        super().__init__()
        self.show_all = show_all
        self.scanner, self.killer, self.interval = scanner, killer, interval
        self.rows: list[Listener] = []
        self.query_text = ""
        self.message = ""

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield Input(placeholder="filter by port, project or kind…", id="filter")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        for label, key, width in [("Port", "port", 6), ("Project", "project", 20), ("What", "kind", 21),
                                  ("Up for", "up", 8), ("PID", "pid", 7), ("Memory", "mem", 8), ("", "status", 34)]:
            t.add_column(label, key=key, width=width)
        self.action_refresh()
        self.set_interval(self.interval, self.action_refresh)
        t.focus()

    # -- data -------------------------------------------------------------------------------------
    def visible(self) -> list[Listener]:
        rows = [l for l in self.rows if self.show_all or l.category != "system"]
        q = self.query_text.lower()
        if q:
            rows = [l for l in rows if q in f"{l.port} {l.project or ''} {l.kind} {l.name} {l.command}".lower()]
        return rows

    def action_refresh(self) -> None:
        self.rows = self.scanner(include_system=True)
        t = self.query_one(DataTable)
        keep = t.cursor_row
        current = self.selected()
        t.clear()
        for l in self.visible():
            style = CAT_STYLE.get(l.category, "white")
            status = Text()
            if l.zombie:
                status.append("zombie? ", "bold red")
                status.append(l.zombie, "red")
            elif l.public and l.category in ("dev", "db"):
                status.append("open to your network", "yellow")
            elif l.category == "system":
                status.append(l.user or "", "bright_black")
            t.add_row(Text(str(l.port), "bold"), Text(l.project or "—", "bold" if l.project else "bright_black"),
                      Text(l.kind, style), human_age(l.uptime) if l.started else "", str(l.pid or ""),
                      human_bytes(l.rss), status, key=f"{l.pid}:{l.port}")
        rows = self.visible()
        if current:
            for i, l in enumerate(rows):
                if (l.pid, l.port) == (current.pid, current.port):
                    keep = i
        if rows:
            t.move_cursor(row=min(keep, len(rows) - 1))
        zombies = [l for l in rows if l.zombie]
        mine = [l for l in self.rows if l.mine]
        head = Text.assemble((f" {len(rows)} ports", "bold"), (" listening", ""),
                             (f"  ·  {len(mine)} yours", "dim"),
                             (f"  ·  {len(zombies)} look{'s' * (len(zombies) == 1)} abandoned", "bold red") if zombies else ("", ""))
        self.query_one("#head", Static).update(head)
        sel = self.selected()
        detail = Text(sel.command[:200], "dim") if sel else Text("")
        if self.message:
            detail = Text(self.message, "yellow")
        self.query_one("#status", Static).update(detail)

    def selected(self) -> Listener | None:
        rows = self.visible()
        t = self.query_one(DataTable)
        if not rows or t.cursor_row is None or t.cursor_row >= len(rows):
            return None
        return rows[t.cursor_row]

    def on_data_table_row_highlighted(self, _) -> None:
        sel = self.selected()
        if sel and not self.message:
            self.query_one("#status", Static).update(Text(sel.command[:200], "dim"))

    # -- actions ------------------------------------------------------------------------------------
    def _kill(self, l: Listener, force: bool) -> None:
        if not l.pid:
            self.message = f"Can't kill port {l.port}: it belongs to another user or the system."
        else:
            result = self.killer(l.pid, force=force)
            self.message = f"Port {l.port} ({l.project or l.kind}): {result}"
        self.set_timer(4, self._clear_message)
        self.action_refresh()

    def _clear_message(self) -> None:
        self.message = ""
        self.action_refresh()

    def action_kill(self) -> None:
        if (l := self.selected()) is not None:
            self._kill(l, force=False)

    def action_force_kill(self) -> None:
        if (l := self.selected()) is not None:
            self._kill(l, force=True)

    def action_kill_zombies(self) -> None:
        zombies = [l for l in self.visible() if l.zombie and l.pid]
        if not zombies:
            self.message = "No zombies. Everything listening looks like it's in use."
            self.set_timer(4, self._clear_message)
            self.action_refresh()
            return
        names = "\n".join(f"  :{l.port}  {l.project or l.kind}  ({l.zombie})" for l in zombies)

        def done(ok: bool | None) -> None:
            if ok:
                results = [f":{l.port} {self.killer(l.pid)}" for l in zombies]
                self.message = "  ".join(results)
                self.set_timer(5, self._clear_message)
            self.action_refresh()

        self.push_screen(Confirm(f"Kill {len(zombies)} abandoned server{'s' * (len(zombies) != 1)}?\n\n{names}"), done)

    def action_open(self) -> None:
        if (l := self.selected()) is not None:
            webbrowser.open(f"http://localhost:{l.port}")

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.action_refresh()

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.add_class("on")
        box.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_text = event.value
        self.action_refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one(DataTable).focus()
        if not event.value:
            self.query_one("#filter", Input).remove_class("on")

    def key_escape(self) -> None:
        box = self.query_one("#filter", Input)
        if box.has_class("on"):
            box.value = ""
            box.remove_class("on")
            self.query_text = ""
            self.query_one(DataTable).focus()
            self.action_refresh()
