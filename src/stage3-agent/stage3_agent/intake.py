"""Input contract enforcement and normalization.

Everything downstream reads `run_dir/normalized/*.csv`, never the original
exports. That is what makes the later steps reproducible: one conversion
point, one declared column set, one hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .errors import ContractError
from .io_utils import read_csv, sha256_file, write_csv
from .schema import CANDIDATES, PANEL, POSES, RECEPTORS, REFERENCE_POSES

ProgressCb = Callable[[int, int, str], None]


@dataclass
class IntakeResult:
    candidates: list[dict[str, str]]
    receptors: list[dict[str, str]]
    poses: list[dict[str, str]]
    panel: list[dict[str, str]]
    reference_poses: list[dict[str, str]]
    input_info: dict[str, Any]
    normalized_paths: dict[str, Path]
    warnings: list[str] = field(default_factory=list)
    primary_receptor: str = "WT"

    def candidate(self, candidate_id: str) -> dict[str, str]:
        for row in self.candidates:
            if row["candidate_id"] == candidate_id:
                return row
        raise KeyError(candidate_id)

    def receptor(self, receptor_id: str) -> dict[str, str]:
        for row in self.receptors:
            if row["receptor_id"] == receptor_id:
                return row
        raise KeyError(receptor_id)

    @property
    def candidates_by_role(self) -> dict[str, list[dict[str, str]]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for candidate in self.candidates:
            grouped.setdefault(candidate["role"], []).append(candidate)
        return grouped


def load_inputs(
    config: Config,
    run_dir: str | Path,
    *,
    progress: ProgressCb | None = None,
) -> IntakeResult:
    run_dir = Path(run_dir)
    normalized_dir = run_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    input_info: dict[str, Any] = {}

    def report(done: int, total: int, message: str) -> None:
        if progress:
            progress(done, total, message)

    paths = {
        "candidates": config.input_path("candidates"),
        "receptors": config.input_path("receptors"),
        "poses": config.input_path("poses"),
        "panel": config.input_path("panel"),
        "reference_poses": config.input_path("reference_poses"),
    }

    report(0, 5, "candidates")
    candidates, info = read_csv(paths["candidates"], CANDIDATES)
    input_info["candidates"] = info
    _require_rows(candidates, "candidates")
    _warn_optional_fields(
        [row for row in candidates if row["role"] == "candidate"],
        "candidate",
        ("smiles", "source", "chemotype"),
        warnings,
    )

    report(1, 5, "receptors")
    receptors, info = read_csv(paths["receptors"], RECEPTORS)
    input_info["receptors"] = info
    _require_rows(receptors, "receptors")

    report(2, 5, "poses")
    if paths["poses"]:
        poses, info = read_csv(paths["poses"], POSES)
        input_info["poses"] = info
        _require_rows(poses, "poses")
    else:
        poses = []
        input_info["poses"] = {"rows": 0, "columns": 0, "ignored_columns": [], "filled_columns": []}

    panel: list[dict[str, str]] = []
    if paths["panel"]:
        report(3, 5, "panel")
        panel, info = read_csv(paths["panel"], PANEL)
        input_info["panel"] = info
    else:
        warnings.append("未提供突变 panel；一致性与耐药分量按中性值处理")

    reference_poses: list[dict[str, str]] = []
    if paths["reference_poses"]:
        report(4, 5, "reference poses")
        reference_poses, info = read_csv(paths["reference_poses"], REFERENCE_POSES)
        input_info["reference_poses"] = info
    else:
        warnings.append("未提供参考配体重对接结果；红对接检查将记为缺口")

    candidate_ids = {row["candidate_id"] for row in candidates}
    receptor_ids = {row["receptor_id"] for row in receptors}
    _check_foreign_keys(poses, candidate_ids, receptor_ids, "poses")
    _check_foreign_keys(panel, candidate_ids, receptor_ids, "panel")
    _check_foreign_keys(reference_poses, candidate_ids, receptor_ids, "reference_poses")

    primary_receptor = _resolve_primary(receptors, config.criteria["primary_receptor"], warnings)

    _check_pose_coverage(candidates, poses, primary_receptor, warnings)
    if poses:
        _check_protocols(poses, warnings)
    _check_heavy_atoms(candidates, poses, warnings)
    _check_developability(candidates, warnings)
    if config.engine == "vina":
        _check_vina_requirements(candidates, receptors)

    normalized_paths: dict[str, Path] = {}
    report(0, 5, "writing normalized tables")
    for name, spec, rows in (
        ("candidates", CANDIDATES, candidates),
        ("receptors", RECEPTORS, receptors),
        ("poses", POSES, poses),
        ("panel", PANEL, panel),
        ("reference_poses", REFERENCE_POSES, reference_poses),
    ):
        if not rows:
            continue
        path = normalized_dir / f"{name}.csv"
        write_csv(path, rows, spec)
        normalized_paths[name] = path

    return IntakeResult(
        candidates=candidates,
        receptors=receptors,
        poses=poses,
        panel=panel,
        reference_poses=reference_poses,
        input_info=input_info,
        normalized_paths=normalized_paths,
        warnings=warnings,
        primary_receptor=primary_receptor,
    )


def _require_rows(rows: list[dict[str, str]], name: str) -> None:
    if not rows:
        raise ContractError(f"{name}: no data rows")


def _warn_optional_fields(
    rows: list[dict[str, str]], label: str, fields: tuple[str, ...], warnings: list[str]
) -> None:
    for field_name in fields:
        missing = [row["candidate_id"] for row in rows if not row.get(field_name)]
        if missing:
            warnings.append(
                f"{len(missing)} 个候选缺少 {field_name}：{', '.join(missing[:6])}"
            )


def _check_foreign_keys(
    rows: list[dict[str, str]],
    candidate_ids: set[str],
    receptor_ids: set[str],
    table: str,
) -> None:
    unknown_candidates = sorted({row["candidate_id"] for row in rows} - candidate_ids)
    unknown_receptors = sorted({row["receptor_id"] for row in rows} - receptor_ids)
    if unknown_candidates:
        raise ContractError(
            f"{table}: unknown candidate_id {unknown_candidates}; "
            f"add them to candidates.csv or fix the ID"
        )
    if unknown_receptors:
        raise ContractError(
            f"{table}: unknown receptor_id {unknown_receptors}; "
            f"add them to receptors.csv or fix the ID"
        )


def _resolve_primary(
    receptors: list[dict[str, str]], configured: str, warnings: list[str]
) -> str:
    ids = [row["receptor_id"] for row in receptors]
    flagged = [row["receptor_id"] for row in receptors if row["is_primary"] == "true"]
    if len(flagged) > 1:
        raise ContractError(f"receptors: more than one is_primary=true ({flagged})")
    if flagged:
        if configured and configured not in ids:
            raise ContractError(
                f"criteria.primary_receptor={configured!r} not in receptors {ids}"
            )
        if configured and configured != flagged[0]:
            warnings.append(
                f"criteria.primary_receptor={configured!r} 被 is_primary={flagged[0]!r} 覆盖"
            )
        return flagged[0]
    if configured in ids:
        return configured
    if "WT" in ids:
        return "WT"
    warnings.append(
        f"没有标记 is_primary，且 {configured!r} 不在受体列表中；暂用 {ids[0]!r}"
    )
    return ids[0]


def _check_pose_coverage(
    candidates: list[dict[str, str]],
    poses: list[dict[str, str]],
    primary_receptor: str,
    warnings: list[str],
) -> None:
    covered = {(row["candidate_id"], row["receptor_id"]) for row in poses}
    if not covered:
        return
    missing = [
        row["candidate_id"]
        for row in candidates
        if row["role"] == "candidate" and (row["candidate_id"], primary_receptor) not in covered
    ]
    if missing:
        raise ContractError(
            f"candidates without a pose at primary receptor {primary_receptor!r}: {missing}. "
            f"Add poses or mark them as controls."
        )
    controls_missing = [
        row["candidate_id"]
        for row in candidates
        if row["role"] == "control" and not any(
            candidate_id == row["candidate_id"] for candidate_id, _ in covered
        )
    ]
    if controls_missing:
        warnings.append(f"对照缺少姿势：{controls_missing}")


def _check_protocols(poses: list[dict[str, str]], warnings: list[str]) -> None:
    primary = {row["protocol"] for row in poses if row["protocol_role"] == "primary"}
    if not primary:
        raise ContractError("poses: no rows with protocol_role=primary")
    if len(primary) > 1:
        warnings.append(
            f"存在多个 primary 协议（{sorted(primary)}）；排序只使用第一个"
        )


def _check_heavy_atoms(
    candidates: list[dict[str, str]], poses: list[dict[str, str]], warnings: list[str]
) -> None:
    declared = {
        row["candidate_id"]: int(row["heavy_atoms"])
        for row in candidates
        if row.get("heavy_atoms")
    }
    mismatch: dict[str, set[str]] = {}
    for row in poses:
        candidate_id = row["candidate_id"]
        if candidate_id in declared and row.get("heavy_atoms"):
            if int(row["heavy_atoms"]) != declared[candidate_id]:
                mismatch.setdefault(candidate_id, set()).add(row["heavy_atoms"])
    for candidate_id, values in mismatch.items():
        warnings.append(
            f"{candidate_id}: 声明的 heavy_atoms={declared[candidate_id]}，"
            f"但姿势表报告 {sorted(values)}"
        )


def _check_developability(candidates: list[dict[str, str]], warnings: list[str]) -> None:
    fields = ("availability", "synthesizability", "solubility_risk", "selectivity_risk")
    incomplete = [
        row["candidate_id"]
        for row in candidates
        if row["role"] == "candidate" and any(not row.get(name) for name in fields)
    ]
    if incomplete:
        warnings.append(
            f"{incomplete} 的成药性四项未填全；按中性 0.5 处理并在不确定性表标记"
        )


def _check_vina_requirements(
    candidates: list[dict[str, str]], receptors: list[dict[str, str]]
) -> None:
    missing_ligand = [
        row["candidate_id"]
        for row in candidates
        if row["role"] in ("candidate", "control") and not row.get("ligand_pdbqt_path")
    ]
    if missing_ligand:
        raise ContractError(
            f"vina engine requires ligand_pdbqt_path for {missing_ligand}"
        )
    missing_receptor = [
        row["receptor_id"]
        for row in receptors
        if not row.get("pdbqt_path")
        or not all(row.get(name) for name in ("box_center_x", "box_center_y", "box_center_z",
                                             "box_size_x", "box_size_y", "box_size_z"))
    ]
    if missing_receptor:
        raise ContractError(
            f"vina engine requires pdbqt_path and box for receptors {missing_receptor}"
        )


def input_hashes(intake: IntakeResult) -> dict[str, str]:
    return {
        name: sha256_file(path) for name, path in sorted(intake.normalized_paths.items())
    }
