"""Live TUI dashboard for the email-crawling pipeline.

Reads input JSON for the total work, tails results.jsonl for progress,
and tails logs/decisions-*.jsonl for token usage and recent activity.

Run in a second terminal alongside pipeline.py:
    uv run python scripts/dashboard.py
"""
import argparse
import json
import os
import time
from collections import Counter, deque
from datetime import date, datetime, timedelta
from pathlib import Path

from rich.align import Align
from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

DEFAULT_INPUT = "data/silver/vereadores-completo.json"
DEFAULT_RESULTS = "data/silver/results.jsonl"
DEFAULT_LOGS = "logs"
RECENT_EVENTS = 12


def fmt_duration(seconds: float) -> str:
    import math
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def load_input_index(path: str) -> dict[int, str]:
    with open(path) as f:
        rows = json.load(f)
    return {row["candidato_seq"]: row["candidato_nome_urna"] for row in rows}


class FollowingFile:
    """Tail-like reader: yields new lines appended since the last call."""

    def __init__(self, path: Path, *, start_at_end: bool = False):
        self.path = path
        self._inode: int | None = None
        self._pos: int = 0
        if start_at_end and path.exists():
            self._inode = path.stat().st_ino
            self._pos = path.stat().st_size

    def read_new(self) -> list[str]:
        if not self.path.exists():
            return []
        st = self.path.stat()
        if self._inode is None:
            self._inode = st.st_ino
        elif st.st_ino != self._inode or st.st_size < self._pos:
            # rotated or truncated
            self._inode = st.st_ino
            self._pos = 0
        if st.st_size == self._pos:
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            f.seek(self._pos)
            data = f.read()
            self._pos = f.tell()
        return [ln for ln in data.splitlines() if ln.strip()]


class DecisionsTail:
    """Follows logs/decisions-YYYY-MM-DD.jsonl, rolling over at midnight."""

    def __init__(self, logs_dir: Path, *, start_at_end: bool):
        self.logs_dir = logs_dir
        self.start_at_end = start_at_end
        self._day = date.today().isoformat()
        self._follower = self._make_follower()

    def _make_follower(self) -> FollowingFile:
        return FollowingFile(
            self.logs_dir / f"decisions-{self._day}.jsonl",
            start_at_end=self.start_at_end,
        )

    def read_new(self) -> list[dict]:
        today = date.today().isoformat()
        if today != self._day:
            self._day = today
            self._follower = FollowingFile(self.logs_dir / f"decisions-{today}.jsonl", start_at_end=False)
        out: list[dict] = []
        for line in self._follower.read_new():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def status_from_record(rec: dict) -> str:
    if rec.get("status"):
        return rec["status"]
    if rec.get("email"):
        return "found"
    return "not_found"


def render(state: "State") -> Layout:
    pct = (state.processed_total / state.total * 100) if state.total else 0
    bar = Progress(
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=None, complete_style="green", finished_style="green"),
        TextColumn("[white]{task.percentage:>5.2f}%[/white]"),
        TextColumn("•"),
        TextColumn("[cyan]{task.completed:,}/{task.total:,}[/cyan]"),
        expand=True,
    )
    bar.add_task("overall ", total=state.total, completed=state.processed_total)

    # Session metrics
    elapsed = time.monotonic() - state.started_at
    session_done = state.processed_total - state.processed_at_start
    rate = session_done / elapsed if elapsed > 0 else 0.0  # per second
    remaining = state.total - state.processed_total
    eta_seconds = (remaining / rate) if rate > 0 else float("inf")
    eta_clock = (
        (datetime.now() + timedelta(seconds=eta_seconds)).strftime("%Y-%m-%d %H:%M")
        if rate > 0 and eta_seconds < 60 * 60 * 24 * 30
        else "—"
    )

    found = state.status_counts.get("found", 0)
    found_rate = (found / state.processed_total * 100) if state.processed_total else 0

    # Token stats (session only)
    total_tokens = state.tokens_prompt + state.tokens_eval
    tok_per_s = total_tokens / elapsed if elapsed > 0 else 0.0

    left = Table.grid(padding=(0, 2), expand=True)
    left.add_column(justify="right", style="dim")
    left.add_column(justify="left", style="bold")
    left.add_row("Total", fmt_int(state.total))
    left.add_row("Done (overall)", f"{fmt_int(state.processed_total)}  [dim]({pct:.2f}%)[/dim]")
    left.add_row("Done (session)", fmt_int(session_done))
    left.add_row("Remaining", fmt_int(remaining))
    left.add_row("Rate", f"{rate * 60:.1f} /min" if rate else "—")
    left.add_row("Elapsed", fmt_duration(elapsed))
    left.add_row("ETA", fmt_duration(eta_seconds))
    left.add_row("Finishes ~", eta_clock)

    right = Table.grid(padding=(0, 2), expand=True)
    right.add_column(justify="right", style="dim")
    right.add_column(justify="left", style="bold")
    right.add_row("found",     f"[green]{fmt_int(found)}[/green]  [dim]({found_rate:.1f}%)[/dim]")
    right.add_row("not_found", f"[yellow]{fmt_int(state.status_counts.get('not_found', 0))}[/yellow]")
    right.add_row("error",     f"[red]{fmt_int(state.status_counts.get('error', 0))}[/red]")
    right.add_row("no_url",    f"[red]{fmt_int(state.status_counts.get('no_url', 0))}[/red]")
    right.add_row("", "")
    right.add_row("AI calls (sess)", fmt_int(state.ai_calls))
    right.add_row("Prompt tokens", fmt_int(state.tokens_prompt))
    right.add_row("Eval tokens", fmt_int(state.tokens_eval))
    right.add_row("Tokens/s", f"{tok_per_s:.1f}")

    stats = Table.grid(expand=True)
    stats.add_column(ratio=1)
    stats.add_column(ratio=1)
    stats.add_row(
        Panel(left, title="progress", border_style="cyan"),
        Panel(right, title="outcomes & tokens", border_style="magenta"),
    )

    activity = Table.grid(padding=(0, 1), expand=True)
    activity.add_column(style="dim", no_wrap=True)
    activity.add_column(style="white", overflow="ellipsis")
    if not state.recent_events:
        activity.add_row("—", "waiting for activity…")
    else:
        for ts, msg in list(state.recent_events)[-RECENT_EVENTS:]:
            activity.add_row(ts.strftime("%H:%M:%S"), msg)

    last_line = (
        f"[dim]results: {state.results_path}  •  input: {state.input_path}  •  "
        f"refresh: {state.refresh:.1f}s  •  ctrl-c to quit[/dim]"
    )

    body = Group(
        Panel(bar, border_style="green"),
        stats,
        Panel(activity, title="recent ai calls", border_style="blue"),
        Align.center(Text.from_markup(last_line)),
    )

    layout = Layout()
    layout.update(
        Panel(
            body,
            title=f"[bold]contato-vereadores[/bold] — {state.model_label}",
            border_style="bright_black",
        )
    )
    return layout


class State:
    def __init__(self, *, input_path: Path, results_path: Path, logs_dir: Path,
                 model_label: str, refresh: float):
        self.input_path = input_path
        self.results_path = results_path
        self.logs_dir = logs_dir
        self.model_label = model_label
        self.refresh = refresh

        self.seq_to_name = load_input_index(str(input_path))
        self.total = len(self.seq_to_name)

        # Pre-existing rows count as already done; the "session" tracks NEW rows.
        self.processed_at_start = count_lines(results_path)
        self.processed_total = self.processed_at_start
        self.status_counts: Counter[str] = Counter()
        # Seed status_counts with pre-existing rows so totals match.
        self._seed_existing_statuses()

        self.results_follower = FollowingFile(results_path, start_at_end=True)
        self.decisions_tail = DecisionsTail(logs_dir, start_at_end=True)

        self.tokens_prompt = 0
        self.tokens_eval = 0
        self.ai_calls = 0
        self.recent_events: deque[tuple[datetime, str]] = deque(maxlen=200)

        self.started_at = time.monotonic()

    def _seed_existing_statuses(self) -> None:
        if not self.results_path.exists():
            return
        with open(self.results_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.status_counts[status_from_record(rec)] += 1

    def tick(self) -> None:
        for line in self.results_follower.read_new():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.processed_total += 1
            self.status_counts[status_from_record(rec)] += 1
            name = self.seq_to_name.get(rec.get("candidato_seq"), "?")
            status = status_from_record(rec)
            tag = {"found": "[green]✓[/green]", "not_found": "[yellow]·[/yellow]",
                   "error": "[red]✗[/red]", "no_url": "[red]∅[/red]"}.get(status, "?")
            self.recent_events.append((datetime.now(), f"{tag} {status:<9} {name}"))

        for ev in self.decisions_tail.read_new():
            self.ai_calls += 1
            self.tokens_prompt += int(ev.get("prompt_eval_count") or 0)
            self.tokens_eval += int(ev.get("eval_count") or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live dashboard for the crawler pipeline.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--results", default=DEFAULT_RESULTS)
    parser.add_argument("--logs-dir", default=DEFAULT_LOGS)
    parser.add_argument("--model", default=os.environ.get("PIPELINE_MODEL", "qwen2.5:14b"))
    parser.add_argument("--refresh", type=float, default=1.0, help="Seconds between refreshes.")
    args = parser.parse_args()

    state = State(
        input_path=Path(args.input),
        results_path=Path(args.results),
        logs_dir=Path(args.logs_dir),
        model_label=args.model,
        refresh=args.refresh,
    )

    with Live(render(state), refresh_per_second=max(1.0, 1.0 / args.refresh), screen=True) as live:
        try:
            while True:
                state.tick()
                live.update(render(state))
                time.sleep(args.refresh)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
