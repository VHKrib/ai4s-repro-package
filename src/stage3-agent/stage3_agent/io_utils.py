"""Deterministic file IO: stable CSV/JSONL writers, atomic JSON, hashes."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .errors import ContractError
from .schema import TableSpec, coerce_value, normalize_row

CSV_ENCODING = "utf-8-sig"  # Excel-friendly, still deterministic
TEXT_ENCODING = "utf-8"


def ensure_dir(path: str | Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=TEXT_ENCODING, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _replace_with_retry(source: str, target: Path, attempts: int = 20) -> None:
    """os.replace can fail on Windows when a reader has the target open.

    `status --follow` polls run_state.json while the pipeline writes it, so a
    short retry loop turns an occasional PermissionError into a non-event.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05)


def atomic_write_json(path: str | Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    atomic_write_text(path, text)


def write_csv(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    spec: TableSpec,
    *,
    validate: bool = True,
) -> int:
    """Write a table with the exact declared column order."""
    path = Path(path)
    ensure_dir(path.parent)
    prepared: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        try:
            prepared.append(normalize_row(spec, row))
        except ContractError as exc:
            raise ContractError(f"{path.name} row {index + 2}: {exc}") from exc
    if validate:
        _check_unique(prepared, spec, path.name)

    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=CSV_ENCODING, newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=spec.column_names, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(prepared)
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return len(prepared)


def read_csv(
    path: str | Path,
    spec: TableSpec | None = None,
    *,
    required: Iterable[str] | None = None,
    key: Iterable[str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Read a CSV against a spec.

    Optional columns may be absent (filled with ""), extra columns are ignored
    but reported. Required columns and key uniqueness are enforced.
    """
    path = Path(path)
    if not path.exists():
        raise ContractError(f"missing input file: {path}")
    with open(path, "r", encoding=CSV_ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ContractError(f"{path.name}: empty file, no header")
        fieldnames = [name.lstrip("﻿").strip() for name in reader.fieldnames]
        reader.fieldnames = fieldnames
        raw_rows = list(reader)

    info: dict[str, Any] = {
        "path": str(path),
        "rows": len(raw_rows),
        "columns": len(fieldnames),
        "ignored_columns": [],
        "filled_columns": [],
    }
    if spec is None:
        if required is None:
            required = []
        missing = [name for name in required if name not in fieldnames]
        if missing:
            raise ContractError(f"{path.name}: missing columns {missing}")
        rows = [{name: (row.get(name) or "").strip() for name in fieldnames} for row in raw_rows]
        return rows, info

    required_set = set(required) if required is not None else {
        col.name for col in spec.columns if not col.nullable
    }
    missing = [name for name in required_set if name not in fieldnames]
    if missing:
        raise ContractError(
            f"{path.name}: missing required columns {missing}; "
            f"expected at least {sorted(required_set)}"
        )
    extra = [name for name in fieldnames if name not in spec.column_names]
    filled = [name for name in spec.column_names if name not in fieldnames]
    info["ignored_columns"] = extra
    info["filled_columns"] = filled

    rows: list[dict[str, str]] = []
    for line_no, raw in enumerate(raw_rows, start=2):
        row: dict[str, str] = {}
        for col in spec.columns:
            value = raw.get(col.name, "")
            try:
                row[col.name] = coerce_value(col, value)
            except ContractError as exc:
                raise ContractError(f"{path.name} row {line_no}: {exc}") from exc
        rows.append(row)

    key_names = list(key) if key is not None else list(spec.key)
    if key_names:
        _check_unique(rows, spec, path.name, key=key_names)
    return rows, info


def _check_unique(
    rows: list[dict[str, str]],
    spec: TableSpec,
    label: str,
    *,
    key: Iterable[str] | None = None,
) -> None:
    names = list(key) if key is not None else list(spec.key)
    if not names:
        return
    seen: dict[tuple, int] = {}
    for index, row in enumerate(rows, start=2):
        composite = tuple(row.get(name, "") for name in names)
        if composite in seen:
            raise ContractError(
                f"{label}: duplicate key {names}={composite} "
                f"(rows {seen[composite]} and {index})"
            )
        seen[composite] = index


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    path = Path(path)
    ensure_dir(path.parent)
    lines = []
    for row in rows:
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding=TEXT_ENCODING))


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding=TEXT_ENCODING).splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode(TEXT_ENCODING)).hexdigest()


def sha256_json(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{rest:02d}s"


def parse_float(value: Any, default: float | None = None) -> float | None:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def parse_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None or str(value).strip() == "":
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    raise ValueError(f"not a bool: {value!r}")
