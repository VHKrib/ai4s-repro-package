"""Composite scoring and ranking.

Every component is a bounded 0-1 transform with its window declared in the
config, not a min-max over the current batch. That keeps a score comparable
between runs when a candidate is added or removed.

The composite is a weighted sum, rounded to 3 decimals. Gates are applied
later and can only lower a decision, never raise it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .intake import IntakeResult
from .io_utils import parse_float
from .qc import QCResult


@dataclass
class RankResult:
    scores: list[dict[str, Any]]
    uncertainties: list[dict[str, Any]] = field(default_factory=list)


def compute(intake: IntakeResult, qc: QCResult, config: Config) -> RankResult:
    criteria = config.criteria
    weights = config.weights
    windows = config.windows
    primary = intake.primary_receptor

    selected = {
        (row["candidate_id"], row["receptor_id"]): row
        for row in qc.pose_rows
        if row["selected"]
    }
    panel_by_candidate: dict[str, list[dict[str, str]]] = {}
    for row in intake.panel:
        panel_by_candidate.setdefault(row["candidate_id"], []).append(row)

    uncertainties: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []

    for candidate in intake.candidates:
        candidate_id = candidate["candidate_id"]
        pose = selected.get((candidate_id, primary), {})
        affinity = parse_float(pose.get("affinity_kcal_mol"))
        ligand_efficiency = parse_float(pose.get("ligand_efficiency"))
        pose_score = parse_float(pose.get("pose_score"))

        affinity_score = _scale(
            affinity, windows["affinity"]["weak"], windows["affinity"]["strong"]
        )
        le_score = _scale(
            ligand_efficiency,
            windows["ligand_efficiency"]["weak"],
            windows["ligand_efficiency"]["strong"],
        )

        consistency_score = _consistency(
            candidate_id, panel_by_candidate.get(candidate_id, []), qc, primary, config, uncertainties
        )
        developability_score = _developability(candidate, uncertainties)
        resistance_score = _resistance(
            candidate_id, panel_by_candidate.get(candidate_id, []), config, uncertainties
        )

        components = {
            "pose": pose_score if pose_score is not None else 0.5,
            "affinity": affinity_score if affinity_score is not None else 0.5,
            "consistency": consistency_score,
            "developability": developability_score,
            "resistance": resistance_score,
        }
        composite = round(
            sum(weights[name] * value for name, value in components.items()), 3
        )
        tier = (
            _tier(composite, config.tiers)
            if candidate["role"] == "candidate" and affinity is not None
            else ""
        )

        scores.append(
            {
                "candidate_id": candidate_id,
                "name": candidate["name"],
                "role": candidate["role"],
                "engine": pose.get("engine") or config.engine,
                "primary_receptor": primary,
                "affinity_kcal_mol": affinity,
                "ligand_efficiency": ligand_efficiency,
                "pose_score": pose_score if pose_score is not None else 0.5,
                "affinity_score": affinity_score if affinity_score is not None else 0.5,
                "consistency_score": consistency_score,
                "developability_score": developability_score,
                "resistance_score": resistance_score,
                "composite_score": composite,
                "tier": tier,
                "rank": None,
                "has_affinity": affinity is not None,
            }
        )

    ranks = sorted(
        [score for score in scores if score["role"] == "candidate"],
        key=lambda score: (
            -score["composite_score"],
            score["affinity_kcal_mol"] if score["affinity_kcal_mol"] is not None else 0.0,
            score["candidate_id"],
        ),
    )
    for position, score in enumerate(ranks, start=1):
        score["rank"] = position

    return RankResult(scores=scores, uncertainties=_dedupe(uncertainties))


def _scale(value: float | None, weak: float, strong: float) -> float | None:
    """Map value onto 0-1 where weak->0 and strong->1 (strong < weak for energy)."""
    if value is None:
        return None
    if weak == strong:
        return 1.0 if value == strong else 0.0
    ratio = (weak - value) / (weak - strong)
    return round(max(0.0, min(1.0, ratio)), 3)


def _consistency(
    candidate_id: str,
    panel_rows: list[dict[str, str]],
    qc: QCResult,
    primary: str,
    config: Config,
    uncertainties: list[dict[str, Any]],
) -> float:
    components: list[float] = []
    usable = [row for row in panel_rows if row.get("rigid_delta") and row.get("flex_delta")]
    if len(usable) >= 2:
        shifts = [
            abs(float(row["flex_delta"]) - float(row["rigid_delta"])) for row in usable
        ]
        components.append(1.0 - min(1.0, (sum(shifts) / len(shifts)) / 1.0))
    else:
        uncertainties.append(
            {
                "uncertainty_id": f"U-METHOD-{candidate_id}",
                "scope": candidate_id,
                "kind": "no_method_comparison",
                "statement": "缺少刚性/柔性协议对照，方法一致性按中性 0.5 处理",
                "impact": "limits_ranking",
                "resolves_with": "补同参数的刚性 panel 或统一协议",
                "status": "open",
            }
        )
    consensus = qc.consensus.get((candidate_id, primary))
    if consensus and consensus.get("overlap_std") is not None:
        limit = float(config.criteria["max_consensus_std"])
        components.append(1.0 - min(1.0, consensus["overlap_std"] / limit) if limit else 1.0)
    if not components:
        return 0.5
    return round(sum(components) / len(components), 3)


def _developability(candidate: dict[str, str], uncertainties: list[dict[str, Any]]) -> float:
    fields = []
    for name, invert in (
        ("availability", False),
        ("synthesizability", False),
        ("solubility_risk", True),
        ("selectivity_risk", True),
    ):
        value = parse_float(candidate.get(name))
        if value is None:
            continue
        fields.append(1.0 - value if invert else value)
    if not fields:
        uncertainties.append(
            {
                "uncertainty_id": f"U-DEV-{candidate['candidate_id']}",
                "scope": candidate["candidate_id"],
                "kind": "developability_unassessed",
                "statement": "可获得性/可合成性/溶解度/选择性四项都未评估，按中性 0.5 处理",
                "impact": "limits_ranking",
                "resolves_with": "补供应商目录、逆合成可行性、logS/PSA 与选择性数据",
                "status": "open",
            }
        )
        return 0.5
    return round(sum(fields) / len(fields), 3)


def _resistance(
    candidate_id: str,
    panel_rows: list[dict[str, str]],
    config: Config,
    uncertainties: list[dict[str, Any]],
) -> float:
    usable = [row for row in panel_rows if row.get("flex_delta")]
    if not usable:
        uncertainties.append(
            {
                "uncertainty_id": f"U-PANEL-{candidate_id}",
                "scope": candidate_id,
                "kind": "no_mutation_panel",
                "statement": "没有突变 panel，耐药负担按中性 0.5 处理",
                "impact": "limits_ranking",
                "resolves_with": "至少覆盖已知耐药位点的柔性侧链对接",
                "status": "open",
            }
        )
        return 0.5
    worst = max(float(row["flex_delta"]) for row in usable)
    score = 1.0 - min(1.0, max(0.0, worst) / 2.0)
    collapses = sum(
        1
        for row in usable
        if row.get("pose_changed") == "true" and row.get("known_resistance") == "true"
    )
    score -= 0.15 * collapses
    return round(max(0.0, min(1.0, score)), 3)


def _tier(composite: float, tiers: dict[str, float]) -> str:
    if composite >= tiers["A"]:
        return "A"
    if composite >= tiers["B"]:
        return "B"
    return "C"


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for row in rows:
        if row["uncertainty_id"] in seen:
            continue
        seen.add(row["uncertainty_id"])
        result.append(row)
    return result
