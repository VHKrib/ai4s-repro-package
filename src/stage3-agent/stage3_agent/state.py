"""Run state machine: atomic run_state.json + append-only events.jsonl.

The state file is the contract between the running pipeline and the `status`
command. It is written atomically so a reader never sees a half-written file,
and it is small enough to poll twice a second.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .errors import Stage3Error
from .io_utils import atomic_write_json, ensure_dir, now_iso, read_json
from .version import PIPELINE_VERSION, STATE_SCHEMA_VERSION

STATE_FILE = "run_state.json"
EVENTS_FILE = "events.jsonl"
ARTIFACT_DIR = "artifacts"
OUTPUT_DIR = "outputs"


class RunState:
    def __init__(self, run_dir: Path, payload: dict[str, Any]) -> None:
        self.run_dir = Path(run_dir)
        self.payload = payload
        self._last_save = 0.0
        self._save_interval = 0.2

    # -- construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        run_dir: str | Path,
        run_id: str,
        case_id: str,
        params: dict[str, Any],
        stages: list[tuple[str, str]],
        config_path: str = "",
        config_sha256: str = "",
    ) -> "RunState":
        run_dir = ensure_dir(run_dir)
        ensure_dir(run_dir / ARTIFACT_DIR)
        ensure_dir(run_dir / OUTPUT_DIR)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            "run_id": run_id,
            "case_id": case_id,
            "status": "pending",
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "finished_at": None,
            "pid": os.getpid(),
            "config_path": str(config_path),
            "config_sha256": config_sha256,
            "params": params,
            "stages": [
                {
                    "index": index,
                    "id": stage_id,
                    "label": label,
                    "status": "pending",
                    "progress": {"done": 0, "total": 0, "unit": ""},
                    "message": "",
                    "started_at": None,
                    "finished_at": None,
                    "duration_s": None,
                    "artifact": None,
                    "fingerprint": None,
                }
                for index, (stage_id, label) in enumerate(stages, start=1)
            ],
            "counters": {},
            "errors": [],
            "outputs": {},
            "summary": "",
        }
        state = cls(run_dir, payload)
        state.save(force=True)
        return state

    @classmethod
    def load(cls, run_dir: str | Path) -> "RunState":
        run_dir = Path(run_dir)
        path = run_dir / STATE_FILE
        if not path.exists():
            raise Stage3Error(f"no run state at {path}")
        payload = read_json(path)
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise Stage3Error(
                f"state schema {payload.get('schema_version')} != {STATE_SCHEMA_VERSION}"
            )
        return cls(run_dir, payload)

    # -- persistence --------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.run_dir / STATE_FILE

    def save(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_save < self._save_interval:
            return
        self.payload["updated_at"] = now_iso()
        atomic_write_json(self.path, self.payload)
        self._last_save = now

    def event(self, level: str, message: str, **data: Any) -> None:
        record = {
            "ts": now_iso(),
            "level": level,
            "stage": data.pop("stage", None),
            "message": message,
        }
        if data:
            record["data"] = data
        ensure_dir(self.run_dir)
        with open(self.run_dir / EVENTS_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- stage helpers ------------------------------------------------------

    def stage(self, stage_id: str) -> dict[str, Any]:
        for stage in self.payload["stages"]:
            if stage["id"] == stage_id:
                return stage
        raise Stage3Error(f"unknown stage {stage_id}")

    def start_run(self) -> None:
        self.payload["status"] = "running"
        self.payload["started_at"] = self.payload.get("started_at") or now_iso()
        self.save(force=True)

    def start_stage(self, stage_id: str, total: int = 1, unit: str = "", message: str = "") -> None:
        stage = self.stage(stage_id)
        stage["status"] = "running"
        stage["started_at"] = now_iso()
        stage["progress"] = {"done": 0, "total": max(0, total), "unit": unit}
        stage["message"] = message
        self.event("info", f"stage start: {stage['label']}", stage=stage_id)
        self.save(force=True)

    def update_stage(
        self,
        stage_id: str,
        *,
        done: int | None = None,
        total: int | None = None,
        increment: int = 0,
        message: str | None = None,
    ) -> None:
        stage = self.stage(stage_id)
        progress = stage["progress"]
        if total is not None:
            progress["total"] = total
        if done is not None:
            progress["done"] = done
        elif increment:
            progress["done"] = progress.get("done", 0) + increment
        if message is not None:
            stage["message"] = message
        self.save()

    def finish_stage(
        self,
        stage_id: str,
        *,
        artifact: str | Path | None = None,
        message: str = "",
        counters: dict[str, Any] | None = None,
        fingerprint: str | None = None,
    ) -> None:
        stage = self.stage(stage_id)
        stage["status"] = "completed"
        stage["finished_at"] = now_iso()
        stage["duration_s"] = round(self._elapsed(stage), 3)
        stage["progress"]["done"] = stage["progress"].get("total", 0)
        stage["message"] = message or stage["message"]
        if artifact is not None:
            stage["artifact"] = str(Path(artifact).relative_to(self.run_dir))
        if fingerprint is not None:
            stage["fingerprint"] = fingerprint
        if counters:
            self.payload["counters"].update(counters)
        self.event(
            "info",
            f"stage done: {stage['label']}",
            stage=stage_id,
            duration_s=stage["duration_s"],
            counters=counters or {},
        )
        self.save(force=True)

    def skip_stage(self, stage_id: str, reason: str) -> None:
        stage = self.stage(stage_id)
        stage["status"] = "skipped"
        stage["finished_at"] = now_iso()
        stage["message"] = reason
        self.event("info", f"stage skipped: {stage['label']}", stage=stage_id, reason=reason)
        self.save(force=True)

    def fail_stage(self, stage_id: str, message: str) -> None:
        stage = self.stage(stage_id)
        stage["status"] = "failed"
        stage["finished_at"] = now_iso()
        stage["duration_s"] = round(self._elapsed(stage), 3)
        stage["message"] = message
        self.payload["errors"].append(
            {"stage": stage_id, "message": message, "at": now_iso()}
        )
        self.payload["status"] = "failed"
        self.payload["finished_at"] = now_iso()
        self.event("error", message, stage=stage_id)
        self.save(force=True)

    def finish_run(self, summary: str = "", outputs: dict[str, Any] | None = None) -> None:
        self.payload["status"] = "completed"
        self.payload["finished_at"] = now_iso()
        self.payload["summary"] = summary
        if outputs:
            self.payload["outputs"] = outputs
        self.event("info", "run completed", summary=summary)
        self.save(force=True)

    def interrupt(self) -> None:
        if self.payload["status"] == "running":
            self.payload["status"] = "interrupted"
            self.payload["finished_at"] = now_iso()
            self.event("warn", "run interrupted")
            self.save(force=True)

    # -- queries ------------------------------------------------------------

    def is_completed(self, stage_id: str) -> bool:
        return self.stage(stage_id)["status"] in ("completed", "skipped")

    def fingerprint_matches(self, stage_id: str, fingerprint: str) -> bool:
        stage = self.stage(stage_id)
        return stage.get("fingerprint") == fingerprint and self.is_completed(stage_id)

    def elapsed_total(self) -> float:
        started = self.payload.get("started_at")
        if not started:
            return 0.0
        try:
            from datetime import datetime

            start = datetime.fromisoformat(started)
            end = datetime.fromisoformat(self.payload.get("finished_at") or now_iso())
            return max(0.0, (end - start).total_seconds())
        except ValueError:
            return 0.0

    @staticmethod
    def _elapsed(stage: dict[str, Any]) -> float:
        if not stage.get("started_at"):
            return 0.0
        from datetime import datetime

        try:
            start = datetime.fromisoformat(stage["started_at"])
            end = datetime.fromisoformat(stage.get("finished_at") or now_iso())
            return max(0.0, (end - start).total_seconds())
        except ValueError:
            return 0.0


# ---------------------------------------------------------------------------
# Run directory helpers
# ---------------------------------------------------------------------------


def list_run_dirs(runs_dir: str | Path) -> list[Path]:
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    runs = [p for p in runs_dir.iterdir() if p.is_dir() and (p / STATE_FILE).exists()]
    return sorted(runs, key=lambda p: (p / STATE_FILE).stat().st_mtime, reverse=True)


def latest_run(runs_dir: str | Path) -> Path | None:
    runs = list_run_dirs(runs_dir)
    return runs[0] if runs else None


def resolve_run(runs_dir: str | Path, run_id: str | None) -> Path:
    if run_id in (None, "", "latest"):
        path = latest_run(runs_dir)
        if path is None:
            raise Stage3Error(f"no runs found under {runs_dir}")
        return path
    path = Path(runs_dir) / run_id
    if not (path / STATE_FILE).exists():
        raise Stage3Error(f"run not found: {run_id}")
    return path


def load_events(run_dir: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    path = Path(run_dir) / EVENTS_FILE
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:] if limit else rows
