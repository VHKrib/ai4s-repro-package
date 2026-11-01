"""Pipeline orchestration: seven stages, fingerprints, resume, artifacts."""

from __future__ import annotations

import platform
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import schema
from .applicability import ApplicabilityResult, run_checks
from .config import Config, load_config
from .decide import DecisionResult, apply as apply_decisions
from .engines import get_engine
from .engines.base import EngineResult
from .errors import ContractError, StageFailed, Stage3Error
from .intake import IntakeResult, input_hashes, load_inputs
from .io_utils import (
    atomic_write_json,
    ensure_dir,
    now_iso,
    read_json,
    sha256_file,
    sha256_json,
    write_csv,
    write_jsonl,
)
from .progress import ProgressPrinter
from .qc import QCResult, evaluate as evaluate_qc
from .rank import RankResult, compute as compute_rank
from .report import write_report
from .state import RunState, resolve_run
from .version import PIPELINE_VERSION, SCHEMA_VERSION

STAGES: list[tuple[str, str]] = [
    ("intake", "输入契约校验"),
    ("applicability", "方法适用性与参考对照"),
    ("poses", "结合姿势 / 复合物数据"),
    ("qc", "姿势几何与一致性检查"),
    ("rank", "综合评估与候选排序"),
    ("decide", "判据与决策"),
    ("export", "输出与清单"),
]


def run(
    config_path: str | Path,
    *,
    run_id: str | None = None,
    resume: bool = False,
    quiet: bool = False,
) -> RunState:
    config = load_config(config_path)
    runs_dir = config.runs_dir
    ensure_dir(runs_dir)

    if resume:
        run_dir = resolve_run(runs_dir, run_id)
        state = RunState.load(run_dir)
        if state.payload["status"] == "completed":
            return state
    else:
        run_id = run_id or f"{config.case_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        run_dir = runs_dir / run_id
        if run_dir.exists():
            run_id = f"{run_id}-{int(time.time()) % 1000:03d}"
            run_dir = runs_dir / run_id
        state = RunState.create(
            run_dir,
            run_id=run_id,
            case_id=config.case_id,
            params={
                "engine": config.engine,
                "engine_options": config.engine_options,
                "criteria": config.criteria,
                "weights": config.weights,
                "target": config.target,
            },
            stages=STAGES,
            config_path=str(config.path),
            config_sha256=config.config_hash,
        )

    printer = ProgressPrinter(state, enabled=not quiet)
    started = time.time()
    state.start_run()
    state.event("info", f"run start: {config.case_id}", engine=config.engine)
    printer.header()

    metrics = _compute_fingerprints(config)
    artifacts: dict[str, Any] = {}

    try:
        intake = _stage_intake(config, state, printer, metrics)
        artifacts["intake"] = intake

        applicability = _stage_applicability(config, state, printer, metrics, intake)
        artifacts["applicability"] = applicability

        engine_result = _stage_poses(config, state, printer, metrics, intake)
        artifacts["poses"] = engine_result

        qc = _stage_qc(config, state, printer, metrics, intake, engine_result)
        artifacts["qc"] = qc

        rank = _stage_rank(config, state, printer, metrics, intake, qc)
        artifacts["rank"] = rank

        decisions = _stage_decide(config, state, printer, metrics, intake, qc, applicability, rank)
        artifacts["decide"] = decisions

        outputs = _stage_export(
            config,
            state,
            printer,
            metrics,
            run_dir,
            intake,
            applicability,
            engine_result,
            qc,
            rank,
            decisions,
            elapsed=time.time() - started,
        )

        summary = _summary(decisions)
        state.finish_run(summary=summary, outputs=outputs)
        printer.stage_done("export")
        printer.footer()
        return state

    except Stage3Error as exc:
        stage_id = getattr(exc, "stage", None) or _running_stage(state)
        state.fail_stage(stage_id, str(exc))
        printer.footer()
        raise
    except KeyboardInterrupt:
        state.interrupt()
        printer.footer()
        raise
    except Exception as exc:  # noqa: BLE001 - surface a clean stage failure
        stage_id = _running_stage(state)
        state.fail_stage(stage_id, f"{type(exc).__name__}: {exc}")
        printer.footer()
        raise StageFailed(stage_id, f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------


def _stage_intake(
    config: Config, state: RunState, printer: ProgressPrinter, fingerprints: dict[str, str]
) -> IntakeResult:
    stage = "intake"
    if _can_resume(state, stage, fingerprints):
        intake = _load_intake(state, stage)
        printer.stage_done(stage)
        return intake

    state.start_stage(stage, total=5, unit="tables", message="读取输入")
    printer.refresh(force=True)
    intake = load_inputs(
        config,
        state.run_dir,
        progress=lambda done, total, message: (
            state.update_stage(stage, done=done, total=total, message=message),
            printer.refresh(),
        ),
    )
    for warning in intake.warnings:
        state.event("warn", warning, stage=stage)
    state.finish_stage(
        stage,
        artifact=_write_artifact(state, stage, fingerprints[stage], _dump_intake(intake)),
        message=(
            f"{len(intake.candidates)} 候选 · {len(intake.receptors)} 受体 · "
            f"{len(intake.poses)} 姿势"
        ),
        counters={
            "candidates": len([c for c in intake.candidates if c["role"] == "candidate"]),
            "controls": len([c for c in intake.candidates if c["role"] != "candidate"]),
            "receptors": len(intake.receptors),
            "poses": len(intake.poses),
            "primary_receptor": intake.primary_receptor,
        },
        fingerprint=fingerprints[stage],
    )
    printer.stage_done(stage)
    return intake


def _stage_applicability(
    config: Config,
    state: RunState,
    printer: ProgressPrinter,
    fingerprints: dict[str, str],
    intake: IntakeResult,
) -> ApplicabilityResult:
    stage = "applicability"
    if _can_resume(state, stage, fingerprints):
        result = _load_applicability(state, stage)
        printer.stage_done(stage)
        return result

    state.start_stage(stage, total=max(1, len(config.reference_checks)), unit="checks")
    printer.refresh(force=True)
    result = run_checks(config, intake)
    state.update_stage(stage, done=max(1, len(result.rows)), total=max(1, len(result.rows)))
    for row in result.rows:
        if row["status"] != "pass":
            state.event("warn", f"{row['check_id']}: {row['status']} — {row['note']}", stage=stage)
    message = result.summary
    state.finish_stage(
        stage,
        artifact=_write_artifact(state, stage, fingerprints[stage], _dump_applicability(result)),
        message=message,
        counters={"reference_status": result.status},
        fingerprint=fingerprints[stage],
    )
    printer.stage_done(stage)
    return result


def _stage_poses(
    config: Config,
    state: RunState,
    printer: ProgressPrinter,
    fingerprints: dict[str, str],
    intake: IntakeResult,
) -> EngineResult:
    stage = "poses"
    if _can_resume(state, stage, fingerprints):
        result = _load_engine(state, stage)
        printer.stage_done(stage)
        return result

    engine = get_engine(config.engine)
    pairs = _pair_count(config.engine, intake)
    state.start_stage(stage, total=max(1, pairs), unit="pairs", message=config.engine)
    printer.refresh(force=True)
    result = engine.generate(
        config,
        intake,
        state.run_dir,
        progress=lambda done, total, message: (
            state.update_stage(stage, done=done, total=total, message=message),
            printer.refresh(),
        ),
    )
    for note in result.notes:
        state.event("info", note, stage=stage)
    state.finish_stage(
        stage,
        artifact=_write_artifact(state, stage, fingerprints[stage], _dump_engine(result)),
        message=f"{len(result.rows)} 姿势 · {len(result.uncertainties)} 条不确定性",
        counters={"pose_rows": len(result.rows)},
        fingerprint=fingerprints[stage],
    )
    printer.stage_done(stage)
    return result


def _stage_qc(
    config: Config,
    state: RunState,
    printer: ProgressPrinter,
    fingerprints: dict[str, str],
    intake: IntakeResult,
    engine_result: EngineResult,
) -> QCResult:
    stage = "qc"
    if _can_resume(state, stage, fingerprints):
        result = _load_qc(state, stage)
        printer.stage_done(stage)
        return result

    state.start_stage(stage, total=max(1, len(engine_result.rows)), unit="poses")
    printer.refresh(force=True)
    result = evaluate_qc(engine_result.rows, config, intake)
    state.update_stage(stage, done=len(engine_result.rows), total=max(1, len(engine_result.rows)))
    selected = sum(1 for row in result.pose_rows if row["selected"])
    state.finish_stage(
        stage,
        artifact=_write_artifact(state, stage, fingerprints[stage], _dump_qc(result)),
        message=f"{selected} 个选中姿势 · {len(result.matrix_rows)} 行突变矩阵",
        counters={"selected_poses": selected, "matrix_rows": len(result.matrix_rows)},
        fingerprint=fingerprints[stage],
    )
    printer.stage_done(stage)
    return result


def _stage_rank(
    config: Config,
    state: RunState,
    printer: ProgressPrinter,
    fingerprints: dict[str, str],
    intake: IntakeResult,
    qc: QCResult,
) -> RankResult:
    stage = "rank"
    if _can_resume(state, stage, fingerprints):
        result = _load_rank(state, stage)
        printer.stage_done(stage)
        return result

    state.start_stage(stage, total=max(1, len(intake.candidates)), unit="candidates")
    printer.refresh(force=True)
    result = compute_rank(intake, qc, config)
    state.update_stage(stage, done=len(intake.candidates), total=max(1, len(intake.candidates)))
    ranked = [row for row in result.scores if row["rank"] is not None]
    state.finish_stage(
        stage,
        artifact=_write_artifact(state, stage, fingerprints[stage], _dump_rank(result)),
        message=f"{len(ranked)} 个候选已排序",
        counters={"ranked_candidates": len(ranked)},
        fingerprint=fingerprints[stage],
    )
    printer.stage_done(stage)
    return result


def _stage_decide(
    config: Config,
    state: RunState,
    printer: ProgressPrinter,
    fingerprints: dict[str, str],
    intake: IntakeResult,
    qc: QCResult,
    applicability: ApplicabilityResult,
    rank: RankResult,
) -> DecisionResult:
    stage = "decide"
    if _can_resume(state, stage, fingerprints):
        result = _load_decide(state, stage)
        printer.stage_done(stage)
        return result

    state.start_stage(stage, total=max(1, len(intake.candidates)), unit="candidates")
    printer.refresh(force=True)
    base = [dict(row) for row in _warning_uncertainties(intake)]
    result = apply_decisions(rank, qc, applicability, intake, config, base)
    state.update_stage(stage, done=len(intake.candidates), total=max(1, len(intake.candidates)))
    counts: dict[str, int] = {}
    for row in result.decisions:
        if row["role"] == "candidate":
            counts[row["decision"]] = counts.get(row["decision"], 0) + 1
    state.finish_stage(
        stage,
        artifact=_write_artifact(state, stage, fingerprints[stage], _dump_decide(result)),
        message=" / ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "无候选",
        counters={f"decision_{key}": value for key, value in counts.items()},
        fingerprint=fingerprints[stage],
    )
    printer.stage_done(stage)
    return result


def _stage_export(
    config: Config,
    state: RunState,
    printer: ProgressPrinter,
    fingerprints: dict[str, str],
    run_dir: Path,
    intake: IntakeResult,
    applicability: ApplicabilityResult,
    engine_result: EngineResult,
    qc: QCResult,
    rank: RankResult,
    decisions: DecisionResult,
    *,
    elapsed: float,
) -> dict[str, str]:
    stage = "export"
    state.start_stage(stage, total=7, unit="files", message="写固定格式产物")
    printer.refresh(force=True)
    out = ensure_dir(run_dir / "outputs")
    outputs: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}

    def emit(index: int, name: str, spec, rows: list[dict[str, Any]], filename: str) -> None:
        path = out / filename
        count = write_csv(path, rows, spec)
        outputs[name] = str(path)
        records[name] = {
            "path": str(path.relative_to(run_dir)),
            "sha256": sha256_file(path),
            "rows": count,
            "schema": spec.name,
        }
        state.update_stage(stage, done=index, total=7, message=filename)
        printer.refresh()

    emit(1, "reference_check", schema.REFERENCE_CHECK, applicability.rows, "reference_check.csv")
    emit(2, "pose_metrics", schema.POSE_METRICS, qc.pose_rows, "pose_metrics.csv")
    emit(3, "receptor_matrix", schema.RECEPTOR_MATRIX, qc.matrix_rows, "receptor_matrix.csv")
    emit(4, "candidates_ranked", schema.CANDIDATES_RANKED, decisions.ranked_rows, "candidates_ranked.csv")
    emit(5, "decisions", schema.DECISIONS, decisions.decisions, "decisions.csv")
    emit(6, "uncertainties", schema.UNCERTAINTIES, decisions.uncertainties, "uncertainties.csv")

    write_jsonl(out / "candidates_ranked.jsonl", decisions.ranked_rows)
    write_jsonl(out / "decisions.jsonl", decisions.decisions)
    write_jsonl(out / "pose_metrics.jsonl", qc.pose_rows)
    for name, path, count in (
        ("candidates_ranked_jsonl", out / "candidates_ranked.jsonl", len(decisions.ranked_rows)),
        ("decisions_jsonl", out / "decisions.jsonl", len(decisions.decisions)),
        ("pose_metrics_jsonl", out / "pose_metrics.jsonl", len(qc.pose_rows)),
    ):
        records[name] = {
            "path": str(path.relative_to(run_dir)),
            "sha256": sha256_file(path),
            "rows": count,
            "schema": "jsonl",
        }

    report_path = out / "stage3_report.md"
    write_report(
        report_path,
        run_id=state.payload["run_id"],
        config=config,
        intake=intake,
        applicability=applicability,
        qc=qc,
        rank=rank,
        decisions=decisions,
        elapsed=elapsed,
        artifacts={
            "候选排序": "outputs/candidates_ranked.csv",
            "姿势指标": "outputs/pose_metrics.csv",
            "受体矩阵": "outputs/receptor_matrix.csv",
            "参考对照": "outputs/reference_check.csv",
            "决策": "outputs/decisions.csv",
            "不确定性": "outputs/uncertainties.csv",
            "人读报告": "outputs/stage3_report.md",
            "运行清单": "manifest.json",
        },
    )
    outputs["report"] = str(report_path)
    records["report"] = {
        "path": str(report_path.relative_to(run_dir)),
        "sha256": sha256_file(report_path),
        "rows": 0,
        "schema": "markdown",
    }

    schemas_dir = ensure_dir(run_dir / "schemas")
    schema_files = {}
    for name, payload in schema.all_schemas().items():
        schema_path = schemas_dir / f"{name}.schema.json"
        atomic_write_json(schema_path, payload)
        schema_files[name] = str(schema_path.relative_to(run_dir))

    manifest = _build_manifest(
        config=config,
        state=state,
        intake=intake,
        applicability=applicability,
        engine_result=engine_result,
        records=records,
        schema_files=schema_files,
        elapsed=elapsed,
    )
    atomic_write_json(run_dir / "manifest.json", manifest)
    outputs["manifest"] = str(run_dir / "manifest.json")

    state.update_stage(stage, done=7, total=7, message="manifest.json")
    printer.refresh()

    validation = validate_outputs(run_dir, manifest)
    if validation["errors"]:
        raise ContractError(
            "export self-check failed: " + "; ".join(validation["errors"][:5])
        )
    state.finish_stage(
        stage,
        artifact=_write_artifact(state, stage, fingerprints[stage], _dump_export(records)),
        message="固定格式产物已写出并通过自检",
        fingerprint=fingerprints[stage],
    )
    return {key: str(Path(value).relative_to(run_dir)) for key, value in outputs.items()}


# ---------------------------------------------------------------------------
# Manifest and validation
# ---------------------------------------------------------------------------


def _build_manifest(
    *,
    config: Config,
    state: RunState,
    intake: IntakeResult,
    applicability: ApplicabilityResult,
    engine_result: EngineResult,
    records: dict[str, dict[str, Any]],
    schema_files: dict[str, str],
    elapsed: float,
) -> dict[str, Any]:
    inputs = []
    for role, path in (
        ("candidates", config.input_path("candidates")),
        ("receptors", config.input_path("receptors")),
        ("poses", config.input_path("poses")),
        ("panel", config.input_path("panel")),
        ("reference_poses", config.input_path("reference_poses")),
    ):
        if not path:
            inputs.append({"role": role, "path": None, "sha256": None, "rows": 0})
            continue
        info = intake.input_info.get(role, {})
        inputs.append(
            {
                "role": role,
                "path": str(path),
                "sha256": sha256_file(path),
                "rows": info.get("rows", 0),
                "ignored_columns": info.get("ignored_columns", []),
                "filled_columns": info.get("filled_columns", []),
            }
        )

    software = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pipeline": PIPELINE_VERSION,
        "vina": _module_version("vina"),
        "rdkit": _module_version("rdkit"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "run_id": state.payload["run_id"],
        "case_id": config.case_id,
        "target": config.target,
        "engine": config.engine,
        "engine_options": config.engine_options,
        "config": {"path": str(config.path), "sha256": config.config_hash},
        "inputs": inputs,
        "normalized": {
            name: {
                "path": str(path.relative_to(state.run_dir)),
                "sha256": sha256_file(path),
            }
            for name, path in sorted(intake.normalized_paths.items())
        },
        "parameters": {
            "criteria": config.criteria,
            "weights": config.weights,
            "tiers": config.tiers,
            "windows": config.windows,
            "reference_checks": config.reference_checks,
        },
        "software": software,
        "reference_status": applicability.status,
        "engine_notes": engine_result.notes,
        "outputs": records,
        "schemas": schema_files,
        "elapsed_s": round(elapsed, 3),
        "started_at": state.payload["started_at"],
        "finished_at": now_iso(),
        "interpretation_limits": [
            "docking score is not measured affinity",
            "a plausible pose is not evidence of functional inhibition",
            "AF3 models are static; multiple samples are not a trajectory",
            "H-bond/salt-bridge counts are geometric proximity, not validated angles",
            "MD is supplementary and does not replace experiments",
        ],
    }


def validate_outputs(run_dir: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Re-read every output, check columns, hashes and (if available) JSON Schema."""
    run_dir = Path(run_dir)
    errors: list[str] = []
    checked: list[str] = []
    try:
        import jsonschema  # type: ignore
    except ImportError:
        jsonschema = None

    for name, record in manifest.get("outputs", {}).items():
        path = run_dir / record["path"]
        if not path.exists():
            errors.append(f"{name}: missing {record['path']}")
            continue
        if record.get("schema") in ("markdown", "jsonl"):
            expected = record.get("sha256")
            actual = sha256_file(path)
            if expected and actual != expected:
                errors.append(f"{name}: sha256 mismatch (manifest {expected[:12]}, file {actual[:12]})")
            checked.append(name)
            continue
        spec = schema.OUTPUT_TABLES.get(record["schema"])
        if spec is None:
            errors.append(f"{name}: unknown schema {record['schema']}")
            continue
        try:
            rows, _ = schema_read(path, spec)
        except ContractError as exc:
            errors.append(f"{name}: {exc}")
            continue
        expected = record.get("sha256")
        actual = sha256_file(path)
        if expected and actual != expected:
            errors.append(f"{name}: sha256 mismatch (manifest {expected[:12]}, file {actual[:12]})")
        if record.get("rows") is not None and len(rows) != record["rows"]:
            errors.append(f"{name}: row count {len(rows)} != manifest {record['rows']}")
        if jsonschema is not None:
            schema_path = run_dir / manifest["schemas"][record["schema"]]
            json_rows = [_typed_row(spec, row) for row in rows]
            try:
                jsonschema.validate(json_rows, read_json(schema_path))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: json-schema {type(exc).__name__}: {str(exc)[:160]}")
        checked.append(name)
    return {"errors": errors, "checked": checked}


def schema_read(path: Path, spec) -> tuple[list[dict[str, str]], dict[str, Any]]:
    from .io_utils import read_csv
    from .schema import validate_columns

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        import csv

        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ContractError(f"{path.name}: no header")
        validate_columns(spec, [name.lstrip("﻿") for name in reader.fieldnames])
    return read_csv(path, spec)


def _typed_row(spec, row: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for col in spec.columns:
        value = row.get(col.name, "")
        if value == "":
            result[col.name] = None
        elif col.dtype == "int":
            result[col.name] = int(float(value))
        elif col.dtype == "float":
            result[col.name] = float(value)
        elif col.dtype == "bool":
            result[col.name] = value == "true"
        else:
            result[col.name] = value
    return result


# ---------------------------------------------------------------------------
# Fingerprints, resume, artifacts
# ---------------------------------------------------------------------------


def _compute_fingerprints(config: Config) -> dict[str, str]:
    input_hashes = {}
    for role, key in (
        ("candidates", "candidates"),
        ("receptors", "receptors"),
        ("poses", "poses"),
        ("panel", "panel"),
        ("reference_poses", "reference_poses"),
    ):
        path = config.input_path(key)
        input_hashes[role] = sha256_file(path) if path and path.exists() else None
    base = {
        "config": config.config_hash,
        "inputs": input_hashes,
        "engine": config.engine,
        "engine_options": config.engine_options,
        "criteria": config.criteria,
        "weights": config.weights,
        "tiers": config.tiers,
        "windows": config.windows,
        "reference_checks": config.reference_checks,
        "pipeline": PIPELINE_VERSION,
    }
    return {stage_id: sha256_json({**base, "stage": stage_id}) for stage_id, _ in STAGES}


def _can_resume(state: RunState, stage: str, fingerprints: dict[str, str]) -> bool:
    if not state.fingerprint_matches(stage, fingerprints[stage]):
        return False
    return (state.run_dir / "artifacts" / f"{_stage_index(stage):02d}_{stage}.json").exists()


def _stage_index(stage: str) -> int:
    for index, (stage_id, _) in enumerate(STAGES, start=1):
        if stage_id == stage:
            return index
    raise KeyError(stage)


def _write_artifact(state: RunState, stage: str, fingerprint: str, payload: dict[str, Any]) -> Path:
    path = state.run_dir / "artifacts" / f"{_stage_index(stage):02d}_{stage}.json"
    atomic_write_json(path, {"stage": stage, "fingerprint": fingerprint, **payload})
    return path


def _read_artifact(state: RunState, stage: str) -> dict[str, Any]:
    path = state.run_dir / "artifacts" / f"{_stage_index(stage):02d}_{stage}.json"
    if not path.exists():
        raise Stage3Error(f"missing artifact for stage {stage}: {path}")
    return read_json(path)


def _load_intake(state: RunState, stage: str) -> IntakeResult:
    payload = _read_artifact(state, stage)
    normalized_paths = {
        name: state.run_dir / value
        for name, value in payload.get("normalized_paths", {}).items()
    }
    return IntakeResult(
        candidates=payload["candidates"],
        receptors=payload["receptors"],
        poses=payload["poses"],
        panel=payload["panel"],
        reference_poses=payload["reference_poses"],
        input_info=payload["input_info"],
        normalized_paths=normalized_paths,
        warnings=payload.get("warnings", []),
        primary_receptor=payload["primary_receptor"],
    )


def _load_applicability(state: RunState, stage: str) -> ApplicabilityResult:
    payload = _read_artifact(state, stage)
    return ApplicabilityResult(**payload["result"])


def _load_engine(state: RunState, stage: str) -> EngineResult:
    payload = _read_artifact(state, stage)
    return EngineResult(**payload["result"])


def _load_qc(state: RunState, stage: str) -> QCResult:
    payload = _read_artifact(state, stage)
    result = payload["result"]
    consensus = {
        (item["candidate_id"], item["receptor_id"]): {
            "n_poses": item.get("n_poses"),
            "overlap_std": item.get("overlap_std"),
            "centroid_std": item.get("centroid_std"),
        }
        for item in result.get("consensus", [])
    }
    return QCResult(
        pose_rows=result["pose_rows"],
        matrix_rows=result["matrix_rows"],
        uncertainties=result.get("uncertainties", []),
        consensus=consensus,
    )


def _load_rank(state: RunState, stage: str) -> RankResult:
    payload = _read_artifact(state, stage)
    return RankResult(**payload["result"])


def _load_decide(state: RunState, stage: str) -> DecisionResult:
    payload = _read_artifact(state, stage)
    return DecisionResult(**payload["result"])


def _dump_intake(intake: IntakeResult) -> dict[str, Any]:
    return {
        "candidates": intake.candidates,
        "receptors": intake.receptors,
        "poses": intake.poses,
        "panel": intake.panel,
        "reference_poses": intake.reference_poses,
        "input_info": intake.input_info,
        "warnings": intake.warnings,
        "primary_receptor": intake.primary_receptor,
        "normalized_paths": {
            name: str(path.relative_to(path.parent.parent))
            for name, path in intake.normalized_paths.items()
        },
    }


def _dump_applicability(result: ApplicabilityResult) -> dict[str, Any]:
    return {"result": asdict(result)}


def _dump_engine(result: EngineResult) -> dict[str, Any]:
    return {"result": asdict(result)}


def _dump_qc(result: QCResult) -> dict[str, Any]:
    return {
        "result": {
            "pose_rows": result.pose_rows,
            "matrix_rows": result.matrix_rows,
            "uncertainties": result.uncertainties,
            "consensus": [
                {"candidate_id": key[0], "receptor_id": key[1], **value}
                for key, value in sorted(result.consensus.items())
            ],
        }
    }


def _dump_rank(result: RankResult) -> dict[str, Any]:
    return {"result": asdict(result)}


def _dump_decide(result: DecisionResult) -> dict[str, Any]:
    return {"result": asdict(result)}


def _dump_export(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"records": records}


def _warning_uncertainties(intake: IntakeResult) -> list[dict[str, Any]]:
    rows = []
    for index, warning in enumerate(intake.warnings, start=1):
        rows.append(
            {
                "uncertainty_id": f"U-INPUT-{index:03d}",
                "scope": "case",
                "kind": "input_warning",
                "statement": warning,
                "impact": "note",
                "resolves_with": "补全输入字段后重跑",
                "status": "open",
            }
        )
    return rows


def _pair_count(engine: str, intake: IntakeResult) -> int:
    if engine == "replay":
        return len(intake.poses)
    if engine == "af3":
        return len(intake.poses) or len(intake.candidates) * len(intake.receptors)
    return len([c for c in intake.candidates if c["role"] in ("candidate", "control")]) * len(
        intake.receptors
    )


def _running_stage(state: RunState) -> str:
    for stage in state.payload["stages"]:
        if stage["status"] == "running":
            return stage["id"]
    for stage in state.payload["stages"]:
        if stage["status"] == "pending":
            return stage["id"]
    return "export"


def _summary(decisions: DecisionResult) -> str:
    counts: dict[str, int] = {}
    for row in decisions.decisions:
        if row["role"] == "candidate":
            counts[row["decision"]] = counts.get(row["decision"], 0) + 1
    parts = " / ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "无候选"
    return parts


def _module_version(name: str) -> str | None:
    try:
        module = __import__(name)
        return getattr(module, "__version__", "installed")
    except Exception:  # noqa: BLE001
        return None


# Re-export for CLI convenience.
__all__ = ["STAGES", "run", "validate_outputs", "load_config"]
