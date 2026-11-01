"""Pose quality control: per-pose score, best-pose selection, mutation matrix.

Selection rule (deterministic):
1. keep poses that pass the geometric gate (overlap, catalytic contacts, clashes);
2. among them take the lowest (most negative) affinity;
3. if none pass, take the lowest affinity anyway and mark the pose rejected by
   geometry, so the downstream decision can fail the candidate instead of the
   pipeline silently dropping it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .intake import IntakeResult
from .io_utils import parse_bool, parse_float

OVERLAP_WEIGHT = 0.6
CONTACT_WEIGHT = 0.4
CLASH_PENALTY = 0.15
POSE_CHANGE_OVERLAP_DROP = 0.05
POSE_CHANGE_CONTACT_DROP = 2


@dataclass
class QCResult:
    pose_rows: list[dict[str, Any]]
    matrix_rows: list[dict[str, Any]]
    uncertainties: list[dict[str, Any]] = field(default_factory=list)
    consensus: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)


def evaluate(rows: list[dict[str, Any]], config: Config, intake: IntakeResult) -> QCResult:
    criteria = config.criteria
    pose_rows = [dict(row) for row in rows]
    for row in pose_rows:
        row["pose_score"] = _pose_score(row)
        row["selected"] = False
        row["selection_reason"] = ""

    primary_protocol = _primary_protocol(pose_rows)
    consensus: dict[tuple[str, str], dict[str, float]] = {}
    uncertainties: list[dict[str, Any]] = []
    single_pose_receptors: dict[str, list[str]] = {}
    clash_missing = 0

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in pose_rows:
        if row["protocol"] != primary_protocol or row["protocol_role"] != "primary":
            continue
        grouped.setdefault((row["candidate_id"], row["receptor_id"]), []).append(row)

    for (candidate_id, receptor_id), group in grouped.items():
        group.sort(key=lambda row: (row["pose_index"],))
        selected = _select_pose(group, criteria)
        selected["selected"] = True
        selected["selection_reason"] = _selection_reason(group, selected, criteria)

        if len(group) >= 3:
            overlaps = [float(row["site_overlap_fraction"]) for row in group if row.get("site_overlap_fraction")]
            centroids = [
                float(row["centroid_distance_to_crystal_A"])
                for row in group
                if row.get("centroid_distance_to_crystal_A")
            ]
            if len(overlaps) >= 3:
                consensus[(candidate_id, receptor_id)] = {
                    "n_poses": len(group),
                    "overlap_std": round(statistics.pstdev(overlaps), 4),
                    "centroid_std": round(statistics.pstdev(centroids), 4) if len(centroids) >= 3 else None,
                }
        else:
            single_pose_receptors.setdefault(candidate_id, []).append(receptor_id)

        if any(row.get("clash_pairs") in ("", None) for row in group):
            clash_missing += 1

    for candidate_id, receptors in sorted(single_pose_receptors.items()):
        uncertainties.append(
            {
                "uncertainty_id": f"U-POSE-{candidate_id}",
                "scope": candidate_id,
                "kind": "single_pose",
                "statement": (
                    f"{len(receptors)} 个受体背景每个只有 1 个姿势，"
                    "无法评估姿势在采样间的稳定性"
                ),
                "impact": "note",
                "resolves_with": "提高 n_poses 或多种子重跑",
                "status": "open",
            }
        )
    if clash_missing:
        uncertainties.append(
            {
                "uncertainty_id": "U-CLASH-TABLE",
                "scope": "case",
                "kind": "clash_not_computed",
                "statement": (
                    f"{clash_missing} 个候选-受体对没有冲突原子计数，"
                    "pose_score 未含冲突惩罚"
                ),
                "impact": "limits_ranking",
                "resolves_with": "用结构文件重算 clash_pairs，或改用 vina 引擎重跑",
                "status": "open",
            }
        )

    matrix_rows = _build_matrix(pose_rows, primary_protocol, intake, criteria)
    return QCResult(
        pose_rows=pose_rows,
        matrix_rows=matrix_rows,
        uncertainties=_dedupe(uncertainties),
        consensus=consensus,
    )


def _pose_score(row: dict[str, Any]) -> float | None:
    overlap = parse_float(row.get("site_overlap_fraction"))
    if overlap is None:
        return None
    contacts = parse_float(row.get("asp25_contacts_under_3.5A"), 0.0) or 0.0
    clashes = parse_float(row.get("clash_pairs"), 0.0) or 0.0
    score = (
        OVERLAP_WEIGHT * overlap
        + CONTACT_WEIGHT * min(contacts / 2.0, 1.0)
        - CLASH_PENALTY * clashes
    )
    return round(max(0.0, min(1.0, score)), 3)


def _primary_protocol(rows: list[dict[str, Any]]) -> str:
    protocols = sorted({row["protocol"] for row in rows if row["protocol_role"] == "primary"})
    if not protocols:
        raise ValueError("no primary protocol among poses")
    return protocols[0]


def _affinity(row: dict[str, Any]) -> float | None:
    return parse_float(row.get("affinity_kcal_mol"))


def _select_pose(group: list[dict[str, Any]], criteria: dict[str, Any]) -> dict[str, Any]:
    eligible = [row for row in group if _passes_geometry(row, criteria)]
    pool = eligible or group
    with_affinity = [row for row in pool if _affinity(row) is not None]
    if not with_affinity:
        return group[0]
    return min(with_affinity, key=lambda row: (_affinity(row), row["pose_index"]))


def _passes_geometry(row: dict[str, Any], criteria: dict[str, Any]) -> bool:
    overlap = parse_float(row.get("site_overlap_fraction"))
    contacts = parse_float(row.get("asp25_contacts_under_3.5A"), 0.0) or 0.0
    clashes = parse_float(row.get("clash_pairs"), 0.0) or 0.0
    if overlap is None:
        return False
    return (
        overlap >= float(criteria["min_site_overlap"])
        and contacts >= int(criteria["min_catalytic_contacts"])
        and clashes <= int(criteria["max_clashes"])
    )


def _selection_reason(
    group: list[dict[str, Any]], selected: dict[str, Any], criteria: dict[str, Any]
) -> str:
    if _affinity(selected) is None:
        return "confidence_only"
    if _passes_geometry(selected, criteria):
        return "geometry_gate_passed;lowest_affinity"
    return "no_pose_passed_geometry_gate;lowest_affinity"


def _build_matrix(
    rows: list[dict[str, Any]],
    primary_protocol: str,
    intake: IntakeResult,
    criteria: dict[str, Any],
) -> list[dict[str, Any]]:
    selected = {
        (row["candidate_id"], row["receptor_id"]): row
        for row in rows
        if row["protocol"] == primary_protocol and row["selected"]
    }
    panel = {(row["candidate_id"], row["receptor_id"]): row for row in intake.panel}

    matrix: list[dict[str, Any]] = []
    for candidate in intake.candidates:
        candidate_id = candidate["candidate_id"]
        wt_affinity = _affinity(selected.get((candidate_id, intake.primary_receptor), {}))
        wt_overlap = parse_float(
            selected.get((candidate_id, intake.primary_receptor), {}).get("site_overlap_fraction")
        )
        wt_contacts = parse_float(
            selected.get((candidate_id, intake.primary_receptor), {}).get("asp25_contacts_under_3.5A")
        )
        for receptor in intake.receptors:
            receptor_id = receptor["receptor_id"]
            pose = selected.get((candidate_id, receptor_id))
            panel_row = panel.get((candidate_id, receptor_id))
            mutation = receptor.get("mutations") or (
                panel_row["mutation"] if panel_row and panel_row["mutation"] != "WT" else ""
            )
            affinity = _affinity(pose) if pose else None
            delta = (
                round(affinity - wt_affinity, 3)
                if affinity is not None and wt_affinity is not None
                else None
            )
            overlap = parse_float(pose.get("site_overlap_fraction")) if pose else None
            contacts = (
                int(parse_float(pose.get("asp25_contacts_under_3.5A"), 0)) if pose else None
            )
            pose_changed = _pose_changed(
                receptor_id, mutation, overlap, contacts, wt_overlap, wt_contacts, panel_row
            )
            known = (
                parse_bool(panel_row["known_resistance"])
                if panel_row and panel_row.get("known_resistance")
                else None
            )
            flex_delta = parse_float(panel_row["flex_delta"]) if panel_row else None
            risk, flags = _risk(
                receptor_id,
                affinity,
                delta,
                flex_delta,
                pose_changed,
                known,
                overlap,
                criteria,
            )
            matrix.append(
                {
                    "candidate_id": candidate_id,
                    "receptor_id": receptor_id,
                    "mutation": mutation,
                    "known_resistance": known,
                    "affinity_kcal_mol": affinity,
                    "delta_vs_wt": delta,
                    "site_overlap_fraction": overlap,
                    "catalytic_contacts": contacts,
                    "pose_changed": pose_changed,
                    "risk_level": risk,
                    "flags": ";".join(flags),
                }
            )
    return matrix


def _pose_changed(
    receptor_id: str,
    mutation: str,
    overlap: float | None,
    contacts: int | None,
    wt_overlap: float | None,
    wt_contacts: float | None,
    panel_row: dict[str, str] | None,
) -> bool | None:
    if panel_row and panel_row.get("pose_changed"):
        return parse_bool(panel_row["pose_changed"])
    if not mutation:
        return False
    if overlap is None or contacts is None or wt_overlap is None or wt_contacts is None:
        return None
    return (wt_overlap - overlap) >= POSE_CHANGE_OVERLAP_DROP or (
        int(wt_contacts) - int(contacts)
    ) >= POSE_CHANGE_CONTACT_DROP


def _risk(
    receptor_id: str,
    affinity: float | None,
    delta: float | None,
    flex_delta: float | None,
    pose_changed: bool | None,
    known: bool | None,
    overlap: float | None,
    criteria: dict[str, Any],
) -> tuple[str, list[str]]:
    flags: list[str] = []
    if affinity is None:
        flags.append("no_affinity")
    if known:
        flags.append("known_resistance")
    if pose_changed:
        flags.append("pose_changed")
    if flex_delta is not None and flex_delta >= float(criteria["max_flex_delta"]):
        flags.append("high_flex_delta")
    if overlap is not None and overlap < float(criteria["min_site_overlap"]):
        flags.append("low_overlap")

    if affinity is None and flex_delta is None:
        return "unknown", flags + ["risk_unassessed"]
    if pose_changed and known:
        return "high", flags
    if (flex_delta is not None and flex_delta >= 1.0) or (pose_changed and overlap is not None and overlap < 0.7):
        return "high", flags
    if flex_delta is not None and flex_delta >= 0.5:
        return "moderate", flags
    if known and flex_delta is not None and flex_delta >= 0.3:
        return "moderate", flags
    if delta is not None and delta >= 0.3:
        return "moderate", flags
    if flex_delta is not None and flex_delta >= 0.2:
        return "low", flags
    if delta is not None and delta >= 0.1:
        return "low", flags
    return "none", flags


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for row in rows:
        if row["uncertainty_id"] in seen:
            continue
        seen.add(row["uncertainty_id"])
        result.append(row)
    return result
