"""Terminal progress rendering and the `status` view.

Uses only ASCII glyphs for bars so it renders in a cp936 Windows console as
well as UTF-8 terminals. All progress data comes from run_state.json, so the
same numbers feed a second terminal running `stage3.py status --follow`.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .io_utils import format_seconds, now_iso
from .state import RunState, load_events

BAR_WIDTH = 12


def _bar(done: int, total: int) -> str:
    if total <= 0:
        return "[" + "-" * BAR_WIDTH + "]"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * BAR_WIDTH))
    return "[" + "#" * filled + "." * (BAR_WIDTH - filled) + "]"


def _status_glyph(status: str) -> str:
    return {
        "pending": ".",
        "running": ">",
        "completed": "x",
        "skipped": "-",
        "failed": "!",
    }.get(status, "?")


class ProgressPrinter:
    """Prints one live line per stage; degrades to plain lines when piped."""

    def __init__(self, state: RunState, enabled: bool = True) -> None:
        self.state = state
        self.enabled = enabled
        self.is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._live = False

    def header(self) -> None:
        if not self.enabled:
            return
        payload = self.state.payload
        print(
            f"run {payload['run_id']}  |  case {payload['case_id']}"
            f"  |  {payload['pipeline_version']}"
        )
        print("-" * 78)

    def refresh(self, *, force: bool = False) -> None:
        if not self.enabled:
            return
        running = [s for s in self.state.payload["stages"] if s["status"] == "running"]
        if not running:
            return
        stage = running[-1]
        total = len(self.state.payload["stages"])
        progress = stage["progress"]
        done, total_units = progress.get("done", 0), progress.get("total", 0)
        elapsed = self.state._elapsed(stage)
        text = (
            f"[{stage['index']}/{total}] {stage['label']:<18}"
            f"{_bar(done, total_units)} {done}/{total_units}"
        )
        if stage.get("message"):
            text += f"  {stage['message']}"
        text += f"  {format_seconds(elapsed)}"
        if self.is_tty:
            print("\r" + text[:110].ljust(110), end="", flush=True)
            self._live = True
        elif force:
            print(text)

    def stage_done(self, stage_id: str) -> None:
        if not self.enabled:
            return
        stage = self.state.stage(stage_id)
        if self._live and self.is_tty:
            print()
            self._live = False
        total = len(self.state.payload["stages"])
        progress = stage["progress"]
        text = (
            f"[{stage['index']}/{total}] {stage['label']:<18}"
            f"{_status_glyph(stage['status'])} "
            f"{progress.get('done', 0)}/{progress.get('total', 0)}"
        )
        if stage.get("message"):
            text += f"  {stage['message']}"
        text += f"  {format_seconds(stage.get('duration_s'))}"
        print(text)

    def footer(self) -> None:
        if not self.enabled:
            return
        payload = self.state.payload
        print("-" * 78)
        if payload["status"] == "completed":
            print(f"done  {payload.get('summary', '')}  {format_seconds(self.state.elapsed_total())}")
            print(f"run dir: {self.state.run_dir}")
        else:
            print(f"status: {payload['status']}  run dir: {self.state.run_dir}")
            for error in payload.get("errors", [])[-3:]:
                print(f"  ! {error['stage']}: {error['message']}")


def render_status(
    state: RunState,
    *,
    events: list[dict[str, Any]] | None = None,
    show_stages: bool = True,
) -> str:
    payload = state.payload
    lines: list[str] = []
    config_sha = (payload.get("config_sha256") or "")[:12]
    lines.append(f"run:    {payload['run_id']}")
    lines.append(
        f"case:   {payload['case_id']}    status: {payload['status']}"
        f"    elapsed: {format_seconds(state.elapsed_total())}"
    )
    lines.append(
        f"engine: {payload.get('params', {}).get('engine', '-')}"
        f"    config: {config_sha or '-'}"
        f"    pid: {payload.get('pid', '-')}"
    )
    lines.append(f"dir:    {state.run_dir}")

    stages = payload["stages"]
    finished = sum(1 for stage in stages if stage["status"] in ("completed", "skipped"))
    running = [stage for stage in stages if stage["status"] == "running"]
    line = f"progress: {finished}/{len(stages)} stages  {_bar(finished, len(stages))}"
    if running:
        stage = running[-1]
        progress = stage["progress"]
        done, total = progress.get("done", 0), progress.get("total", 0)
        line += f"  current: {stage['label']} {done}/{total}"
        elapsed_stage = state._elapsed(stage)
        if done and total and elapsed_stage > 2:
            eta = elapsed_stage / done * max(0, total - done)
            line += f"  ETA ~{format_seconds(eta)}"
    lines.append(line)
    lines.append("")

    if show_stages:
        lines.append("STAGES")
        lines.append("  #  stage                 status      progress              elapsed  detail")
        for stage in payload["stages"]:
            progress = stage["progress"]
            progress_text = f"{progress.get('done', 0)}/{progress.get('total', 0)}"
            if progress.get("unit"):
                progress_text += f" {progress['unit']}"
            detail = stage.get("message") or ""
            if stage["status"] == "failed":
                detail = detail or "failed"
            lines.append(
                f"  {stage['index']}  {stage['id']:<20}{stage['status']:<12}"
                f"{progress_text:<22}{format_seconds(stage.get('duration_s')):<9}"
                f"{detail}"
            )
        lines.append("")

    counters = payload.get("counters") or {}
    if counters:
        lines.append("COUNTERS")
        width = 0
        items = list(counters.items())
        for index in range(0, len(items), 4):
            chunk = items[index : index + 4]
            lines.append("  " + "   ".join(f"{k}={v}" for k, v in chunk))
        lines.append("")

    if events:
        lines.append("RECENT EVENTS")
        for event in events[-8:]:
            ts = event.get("ts", "")[11:19]
            stage = event.get("stage") or "-"
            lines.append(f"  {ts}  {event.get('level', 'info'):<5} {stage:<12}{event.get('message', '')}")
        lines.append("")

    errors = payload.get("errors") or []
    if errors:
        lines.append("ERRORS")
        for error in errors:
            lines.append(f"  {error.get('at', '')[:19]}  [{error.get('stage')}] {error.get('message')}")
        lines.append("")

    outputs = payload.get("outputs") or {}
    if outputs:
        lines.append("OUTPUTS")
        for name, path in outputs.items():
            lines.append(f"  {name:<20}{path}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def print_status(runs_dir: str | Path, run_dir: Path, *, as_json: bool = False) -> None:
    state = RunState.load(run_dir)
    if as_json:
        print(json.dumps(state.payload, ensure_ascii=False, indent=2))
        return
    events = load_events(run_dir, limit=12)
    print(render_status(state, events=events))


def follow(run_dir: str | Path, *, interval: float = 1.0) -> None:
    run_dir = Path(run_dir)
    last_len = 0
    while True:
        state = RunState.load(run_dir)
        text = render_status(state, events=load_events(run_dir, limit=12))
        if getattr(sys.stdout, "isatty", lambda: False)():
            # Pad to the previous frame length to avoid leftovers.
            print("\033[2J\033[H" + text.ljust(last_len), end="", flush=True)
        else:
            print(f"--- {now_iso()} ---")
            print(text, end="")
        last_len = len(text)
        if state.payload["status"] in ("completed", "failed", "interrupted"):
            break
        time.sleep(interval)
