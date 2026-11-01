"""Preset criteria and candidate decisions.

Gates can only lower a decision. A failed reference control makes the whole
ranking untrustworthy, so it forces `needs_review` rather than a local
`revise` of one candidate. Failed candidates keep their reasons so stage 02
can use them when redesigning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .applicability import ApplicabilityResult
from .config import Config
from .intake import IntakeResult
from .io_utils import parse_bool, parse_float
from .qc import QCResult
from .rank import RankResult


@dataclass
class DecisionResult:
    ranked_rows: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    uncertainties: list[dict[str, Any]]


def apply(
    rank_result: RankResult,
    qc: QCResult,
    applicability: ApplicabilityResult,
    intake: IntakeResult,
    config: Config,
    base_uncertainties: list[dict[str, Any]],
) -> DecisionResult:
    criteria = config.criteria
    primary = intake.primary_receptor
    selected = {
        (row["candidate_id"], row["receptor_id"]): row
        for row in qc.pose_rows
        if row["selected"]
    }
    matrix_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in qc.matrix_rows:
        matrix_by_candidate.setdefault(row["candidate_id"], []).append(row)
    panel_by_candidate: dict[str, list[dict[str, str]]] = {}
    for row in intake.panel:
        panel_by_candidate.setdefault(row["candidate_id"], []).append(row)

    uncertainties = _dedupe(
        base_uncertainties
        + applicability.uncertainties
        + qc.uncertainties
        + rank_result.uncertainties
    )

    ranked_rows: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for score in rank_result.scores:
        candidate_id = score["candidate_id"]
        pose = selected.get((candidate_id, primary), {})
        hard, soft = _gates(
            score,
            pose,
            matrix_by_candidate.get(candidate_id, []),
            panel_by_candidate.get(candidate_id, []),
            applicability,
            criteria,
            config,
        )
        decision, next_stage, reason = _decision(score, hard, soft, applicability, config)
        codes = sorted(set(hard + soft))
        uncertainty_count = _count_uncertainties(uncertainties, candidate_id)

        ranked_rows.append(
            {
                "rank": score["rank"],
                "candidate_id": candidate_id,
                "name": score["name"],
                "role": score["role"],
                "tier": score["tier"],
                "decision": decision,
                "composite_score": score["composite_score"],
                "pose_score": score["pose_score"],
                "affinity_score": score["affinity_score"],
                "consistency_score": score["consistency_score"],
                "developability_score": score["developability_score"],
                "resistance_score": score["resistance_score"],
                "affinity_kcal_mol": score["affinity_kcal_mol"],
                "ligand_efficiency": score["ligand_efficiency"],
                "primary_receptor": score["primary_receptor"],
                "engine": score["engine"],
                "uncertainty_count": uncertainty_count,
                "decision_codes": ";".join(codes),
            }
        )
        decisions.append(
            {
                "candidate_id": candidate_id,
                "name": score["name"],
                "role": score["role"],
                "tier": score["tier"],
                "composite_score": score["composite_score"],
                "decision": decision,
                "decision_codes": ";".join(codes),
                "decision_reason": reason,
                "next_stage": next_stage,
                "reviewer_required": _reviewer_required(decision),
                "hard_failures": ";".join(hard),
                "soft_flags": ";".join(soft),
            }
        )

    ranked_rows.sort(
        key=lambda row: (
            row["role"] != "candidate",
            row["rank"] if row["rank"] is not None else 9999,
            row["candidate_id"],
        )
    )
    decisions.sort(
        key=lambda row: (
            row["role"] != "candidate",
            -row["composite_score"],
            row["candidate_id"],
        )
    )
    return DecisionResult(
        ranked_rows=ranked_rows, decisions=decisions, uncertainties=uncertainties
    )


def _gates(
    score: dict[str, Any],
    pose: dict[str, Any],
    matrix_rows: list[dict[str, Any]],
    panel_rows: list[dict[str, str]],
    applicability: ApplicabilityResult,
    criteria: dict[str, Any],
    config: Config,
) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    if score["role"] != "candidate":
        return hard, soft

    if applicability.status == "fail":
        hard.append("reference_check_failed")
    elif applicability.status == "gap" and criteria["require_reference_pass"]:
        hard.append("reference_check_gap")
    elif applicability.status == "warn":
        soft.append("ranking_unvalidated")

    affinity = score["affinity_kcal_mol"]
    if affinity is None:
        hard.append("no_binding_evidence")
    else:
        if affinity > float(criteria["max_affinity_kcal_mol"]):
            hard.append("affinity_weaker_than_cutoff")
        le = score.get("ligand_efficiency")
        if le is not None and le < float(criteria["min_ligand_efficiency"]):
            hard.append("ligand_efficiency_below_min")

    overlap = parse_float(pose.get("site_overlap_fraction"))
    if overlap is not None:
        if overlap < float(criteria["min_site_overlap"]):
            hard.append("site_overlap_below_min")
    contacts_raw = pose.get("asp25_contacts_under_3.5A")
    if contacts_raw not in (None, ""):
        contacts = parse_float(contacts_raw, 0.0) or 0.0
        if contacts < int(criteria["min_catalytic_contacts"]):
            hard.append("catalytic_contacts_below_min")
    clashes_raw = pose.get("clash_pairs")
    if clashes_raw not in (None, ""):
        clashes = parse_float(clashes_raw, 0.0) or 0.0
        if clashes > int(criteria["max_clashes"]):
            hard.append("clashes")

    mutations = [row for row in matrix_rows if row["mutation"]]
    flex_values = [
        parse_float(row["flex_delta"])
        for row in panel_rows
        if row.get("flex_delta") not in (None, "")
    ]
    flex_values = [value for value in flex_values if value is not None]
    if flex_values and max(flex_values) >= float(criteria["max_flex_delta"]):
        soft.append("high_mutation_penalty")
    if any(
        parse_bool(row.get("pose_changed")) is True
        and parse_bool(row.get("known_resistance")) is True
        for row in panel_rows
    ):
        soft.append("pose_collapse_at_known_site")
    if mutations and all(row["risk_level"] == "unknown" for row in mutations):
        soft.append("mutation_risk_unassessed")

    if config.engine == "af3":
        soft.append("af3_confidence_only")
    if score.get("affinity_kcal_mol") is None:
        soft.append("confidence_only")
    return hard, sorted(set(soft))


def _decision(
    score: dict[str, Any],
    hard: list[str],
    soft: list[str],
    applicability: ApplicabilityResult,
    config: Config,
) -> tuple[str, str, str]:
    if score["role"] != "candidate":
        return "control_only", "", "对照/参考条目，不参与候选决策"

    if "reference_check_failed" in hard:
        return (
            "needs_review",
            "review",
            "参考对照失败：当前对接流程无法复现已知结果，排序不能直接用于决策；"
            "需补对照或人工复核后再用",
        )
    if "reference_check_gap" in hard:
        return (
            "needs_review",
            "review",
            "缺少方法适用性对照，只记录了校验缺口；需人工判断该流程是否适用于本体系",
        )
    if "no_binding_evidence" in hard:
        return (
            "needs_review",
            "review",
            "只有结构/置信度证据，没有结合能数据；不能据此进入实验排序",
        )
    if hard:
        return (
            "revise",
            "02",
            "未达到预设计算筛选标准：" + _humanize(hard) + "；返回阶段 02 重新设计并保留失败原因",
        )
    if soft:
        return (
            "advance_with_flags",
            "04",
            "达到筛选标准但带风险标记：" + _humanize(soft) + "；建议连同标记一起进入实验验证",
        )
    return "advance", "04", "达到预设计算筛选标准，优先进入实验验证"


def _humanize(codes: list[str]) -> str:
    return "、".join(_CODE_LABELS.get(code, code) for code in codes)


_CODE_LABELS = {
    "reference_check_failed": "参考对照未通过",
    "reference_check_gap": "缺少参考对照",
    "no_binding_evidence": "无结合能证据",
    "affinity_weaker_than_cutoff": "对接分数弱于阈值",
    "ligand_efficiency_below_min": "配体效率低于阈值",
    "site_overlap_below_min": "结合位点重叠不足",
    "catalytic_contacts_below_min": "催化残基接触不足",
    "clashes": "存在原子冲突",
    "high_mutation_penalty": "突变背景能量惩罚偏大",
    "pose_collapse_at_known_site": "已知耐药位点上姿势崩塌",
    "mutation_risk_unassessed": "突变风险无法评估",
    "af3_confidence_only": "仅 AF3 置信度",
    "confidence_only": "仅置信度证据",
    "ranking_unvalidated": "排序参考未通过（只能同系列相对比较）",
}


def _reviewer_required(decision: str) -> bool:
    return decision in ("advance_with_flags", "needs_review", "revise")


def _count_uncertainties(rows: list[dict[str, Any]], candidate_id: str) -> int:
    kinds: set[str] = set()
    for row in rows:
        scope = str(row.get("scope", ""))
        if (
            row.get("impact") == "blocks_advance"
            or scope == "case"
            or scope.startswith(candidate_id)
        ):
            kinds.add(str(row.get("kind", "")))
    return len(kinds)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for row in rows:
        key = row["uncertainty_id"]
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result
