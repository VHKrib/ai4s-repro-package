"""Method applicability and reference control checks.

This stage answers one question before any ranking is trusted: has this
docking protocol been shown to work on something we already know the answer
to? A failed or missing control downgrades every downstream decision; it is
never silently treated as a pass.
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .errors import ContractError
from .intake import IntakeResult
from .io_utils import parse_float


@dataclass
class ApplicabilityResult:
    rows: list[dict[str, Any]]
    status: str  # pass | warn | fail | gap | not_evaluated
    summary: str
    uncertainties: list[dict[str, Any]] = field(default_factory=list)
    required_failures: list[str] = field(default_factory=list)
    ranking_warnings: list[str] = field(default_factory=list)


def run_checks(config: Config, intake: IntakeResult) -> ApplicabilityResult:
    checks = list(config.reference_checks)
    rows: list[dict[str, Any]] = []
    uncertainties: list[dict[str, Any]] = []
    required_failures: list[str] = []
    ranking_warnings: list[str] = []
    required_gaps: list[str] = []

    if not checks:
        rows.append(
            _row(
                check_id="reference_absent",
                check_type="reference_absent",
                scope=config.case_id,
                metric_name="checks_declared",
                metric_value=0.0,
                threshold=None,
                comparator="none",
                status="gap",
                source="config",
                note="config 未声明任何参考对照；本轮排序只能当作相对比较",
            )
        )
        uncertainties.append(
            _uncertainty(
                "U-REF-000",
                scope="case",
                kind="reference_absent",
                statement="没有任何参考对照检查，无法证明该对接流程在本体系上有效",
                impact="limits_ranking",
                resolves_with="补红对接 RMSD 或已知药物效价次序对照",
            )
        )
        return ApplicabilityResult(
            rows=rows,
            status="gap",
            summary="无参考对照",
            uncertainties=uncertainties,
        )

    for check in checks:
        check_id = str(check["id"])
        check_type = str(check["type"])
        required = bool(check.get("required", True))
        impact = str(check.get("impact", "blocks_advance"))
        try:
            produced = _dispatch(check, config, intake)
        except ContractError as exc:
            produced = [
                _row(
                    check_id=check_id,
                    check_type=check_type,
                    scope=config.case_id,
                    metric_name="",
                    metric_value=None,
                    threshold=parse_float(check.get("threshold")),
                    comparator=check.get("comparator", "none"),
                    status="gap",
                    source="inputs",
                    note=f"无法计算：{exc}",
                )
            ]
        for row in produced:
            row["check_id"] = check_id if len(produced) == 1 else f"{check_id}:{row['scope']}"
            rows.append(row)

        failed = [row for row in produced if row["status"] == "fail"]
        gapped = [row for row in produced if row["status"] == "gap"]
        if required and failed:
            if impact == "blocks_advance":
                required_failures.append(check_id)
            else:
                ranking_warnings.append(check_id)
            uncertainties.append(
                _uncertainty(
                    f"U-REF-{check_id}",
                    scope=check_id,
                    kind=f"{check_type}_failed",
                    statement=(
                        f"参考对照 {check_id} 未通过："
                        + "; ".join(row.get("note", "") for row in failed)
                    ),
                    impact=impact,
                    resolves_with=_resolution_hint(check_type),
                )
            )
        if required and gapped and not failed:
            required_gaps.append(check_id)
            uncertainties.append(
                _uncertainty(
                    f"U-GAP-{check_id}",
                    scope=check_id,
                    kind=f"{check_type}_gap",
                    statement=f"参考对照 {check_id} 无可用数据，只能记录校验缺口",
                    impact="limits_ranking",
                    resolves_with="补参考配体/参考复合物或已知活性数据",
                )
            )

    status = _aggregate_status(required_failures, ranking_warnings, required_gaps)
    summary = _summary_text(rows, status)
    return ApplicabilityResult(
        rows=rows,
        status=status,
        summary=summary,
        uncertainties=uncertainties,
        required_failures=required_failures,
        ranking_warnings=ranking_warnings,
    )


def _dispatch(check: dict[str, Any], config: Config, intake: IntakeResult) -> list[dict[str, Any]]:
    check_type = check["type"]
    if check_type == "redock_rmsd":
        return _redock_rmsd(check, intake)
    if check_type == "control_ordering":
        return _control_ordering(check, intake)
    if check_type == "mutation_trend":
        return _mutation_trend(check, intake)
    if check_type == "method_agreement":
        return _method_agreement(check, intake)
    if check_type == "engine_availability":
        return _engine_availability(check, config, intake)
    if check_type == "protocol_consistency":
        return _protocol_consistency(check, intake)
    raise ContractError(f"unknown reference check type {check_type!r}")


def _redock_rmsd(check: dict[str, Any], intake: IntakeResult) -> list[dict[str, Any]]:
    candidate_id = str(check["candidate_id"])
    receptor_id = str(check.get("receptor_id", "WT"))
    threshold = float(check.get("threshold", 2.0))
    rows = [
        row
        for row in intake.reference_poses
        if row["candidate_id"] == candidate_id and row["receptor_id"] == receptor_id
    ]
    if not rows:
        return [
            _row(
                check_id=check["id"],
                check_type="redock_rmsd",
                scope=f"{candidate_id}@{receptor_id}",
                metric_name="best_rmsd_A",
                metric_value=None,
                threshold=threshold,
                comparator="le",
                status="gap",
                source="reference_poses",
                note="没有该参考配体的重对接姿势",
            )
        ]
    best = min(rows, key=lambda row: float(row["rmsd_to_reference_A"]))
    best_rmsd = float(best["rmsd_to_reference_A"])
    # Top-ranked pose = pose with best affinity when available.
    with_affinity = [row for row in rows if row.get("affinity_kcal_mol")]
    top_pose = min(with_affinity, key=lambda row: float(row["affinity_kcal_mol"])) if with_affinity else None
    note_parts = []
    if top_pose is not None:
        note_parts.append(
            f"最高分姿势 RMSD={float(top_pose['rmsd_to_reference_A']):.2f} A "
            f"({'通过' if float(top_pose['rmsd_to_reference_A']) <= threshold else '未通过'})"
        )
    note_parts.append("红对接只验证姿势复现，不验证打分排序")
    return [
        _row(
            check_id=check["id"],
            check_type="redock_rmsd",
            scope=f"{candidate_id}@{receptor_id}",
            metric_name="best_rmsd_A",
            metric_value=best_rmsd,
            threshold=threshold,
            comparator="le",
            status="pass" if best_rmsd <= threshold else "fail",
            source="reference_poses",
            note="; ".join(note_parts),
        )
    ]


def _best_affinity_by_candidate(
    intake: IntakeResult, receptor_id: str, candidate_ids: list[str]
) -> dict[str, float]:
    best: dict[str, float] = {}
    for row in intake.poses:
        if row["receptor_id"] != receptor_id or row["protocol_role"] != "primary":
            continue
        if row["candidate_id"] not in candidate_ids or not row.get("affinity_kcal_mol"):
            continue
        value = float(row["affinity_kcal_mol"])
        current = best.get(row["candidate_id"])
        if current is None or value < current:
            best[row["candidate_id"]] = value
    return best


def _control_ordering(check: dict[str, Any], intake: IntakeResult) -> list[dict[str, Any]]:
    receptor_id = str(check.get("receptor_id", "WT"))
    expected = [str(name) for name in check.get("expected", [])]
    if len(expected) < 2:
        raise ContractError("control_ordering needs at least two candidates in 'expected'")
    observed = _best_affinity_by_candidate(intake, receptor_id, expected)
    missing = [name for name in expected if name not in observed]
    if missing:
        return [
            _row(
                check_id=check["id"],
                check_type="control_ordering",
                scope=f"@{receptor_id}",
                metric_name="concordant_pairs",
                metric_value=None,
                threshold=1.0,
                comparator="ge",
                status="gap",
                source="poses",
                note=f"缺少对照的对接分数：{missing}",
            )
        ]
    pairs = 0
    concordant = 0
    reversals = []
    for i, better in enumerate(expected):
        for worse in expected[i + 1 :]:
            pairs += 1
            if observed[better] < observed[worse]:
                concordant += 1
            else:
                reversals.append(f"{better}({observed[better]:.2f}) vs {worse}({observed[worse]:.2f})")
    ratio = concordant / pairs if pairs else 0.0
    observed_order = sorted(expected, key=lambda name: observed[name])
    note = "观察次序 " + " < ".join(f"{name}:{observed[name]:.2f}" for name in observed_order)
    if reversals:
        note += "; 倒置：" + ", ".join(reversals)
    return [
        _row(
            check_id=check["id"],
            check_type="control_ordering",
            scope=f"@{receptor_id}",
            metric_name="concordant_pairs",
            metric_value=ratio,
            threshold=1.0,
            comparator="ge",
            status="pass" if ratio >= 0.999 else "fail",
            source="poses",
            note=note,
        )
    ]


def _mutation_trend(check: dict[str, Any], intake: IntakeResult) -> list[dict[str, Any]]:
    candidate_id = str(check["candidate_id"])
    threshold = float(check.get("threshold", 0.3))
    rows = [row for row in intake.panel if row["candidate_id"] == candidate_id]
    known = [float(row["flex_delta"]) for row in rows if row["known_resistance"] == "true" and row.get("flex_delta")]
    other = [float(row["flex_delta"]) for row in rows if row["known_resistance"] == "false" and row.get("flex_delta")]
    if not known or not other:
        return [
            _row(
                check_id=check["id"],
                check_type="mutation_trend",
                scope=candidate_id,
                metric_name="known_minus_other_flex_delta",
                metric_value=None,
                threshold=threshold,
                comparator="ge",
                status="gap",
                source="panel",
                note="缺少已知耐药位点标注或柔性 panel 数值",
            )
        ]
    separation = sum(known) / len(known) - sum(other) / len(other)
    return [
        _row(
            check_id=check["id"],
            check_type="mutation_trend",
            scope=candidate_id,
            metric_name="known_minus_other_flex_delta",
            metric_value=separation,
            threshold=threshold,
            comparator="ge",
            status="pass" if separation >= threshold else "fail",
            source="panel",
            note=(
                f"已知位点均值 {sum(known)/len(known):+.2f}，其余 {sum(other)/len(other):+.2f}；"
                f"阈值 {threshold:+.2f} kcal/mol"
            ),
        )
    ]


def _method_agreement(check: dict[str, Any], intake: IntakeResult) -> list[dict[str, Any]]:
    candidate_id = str(check["candidate_id"])
    threshold = float(check.get("threshold", 0.5))
    rows = [
        row
        for row in intake.panel
        if row["candidate_id"] == candidate_id
        and row.get("rigid_delta")
        and row.get("flex_delta")
    ]
    if len(rows) < 3:
        return [
            _row(
                check_id=check["id"],
                check_type="method_agreement",
                scope=candidate_id,
                metric_name="pearson_rigid_vs_flex",
                metric_value=None,
                threshold=threshold,
                comparator="ge",
                status="gap",
                source="panel",
                note="少于 3 个突变对，无法计算方法一致性",
            )
        ]
    rigid = [float(row["rigid_delta"]) for row in rows]
    flex = [float(row["flex_delta"]) for row in rows]
    r = _pearson(rigid, flex)
    shifts = [abs(b - a) for a, b in zip(rigid, flex)]
    mean_shift = sum(shifts) / len(shifts)
    if r is None:
        status = "gap"
        note = "数值方差为零，相关系数无定义"
    else:
        status = "pass" if r >= threshold else "fail"
        note = f"刚性 vs 柔性侧链 delta 相关 r={r:+.2f}；平均偏移 {mean_shift:.2f} kcal/mol"
    return [
        _row(
            check_id=check["id"],
            check_type="method_agreement",
            scope=candidate_id,
            metric_name="pearson_rigid_vs_flex",
            metric_value=r,
            threshold=threshold,
            comparator="ge",
            status=status,
            source="panel",
            note=note,
        )
    ]


def _engine_availability(
    check: dict[str, Any], config: Config, intake: IntakeResult
) -> list[dict[str, Any]]:
    engine = config.engine
    if engine == "replay":
        status, note = "pass", "replay 引擎只读取规范化表格"
    elif engine == "vina":
        found = importlib.util.find_spec("vina") is not None
        status = "pass" if found else "fail"
        note = "vina python 包可用" if found else "未安装 vina，无法执行对接"
    elif engine == "af3":
        manifest = config.resolve(check.get("manifest", config.engine_options.get("manifest", "")))
        if not str(manifest) or not manifest.exists():
            status, note = "fail", f"AF3 清单不存在：{manifest}"
        else:
            status, note = "pass", "AF3 清单存在"
    else:
        status, note = "gap", f"未知引擎 {engine}"
    return [
        _row(
            check_id=check["id"],
            check_type="engine_availability",
            scope=engine,
            metric_name="available",
            metric_value=1.0 if status == "pass" else 0.0,
            threshold=1.0,
            comparator="eq",
            status=status,
            source="runtime",
            note=note,
        )
    ]


def _protocol_consistency(check: dict[str, Any], intake: IntakeResult) -> list[dict[str, Any]]:
    primary = {row["protocol"] for row in intake.poses if row["protocol_role"] == "primary"}
    if not primary:
        status = "gap"
        note = "没有 primary 协议"
    elif len(primary) == 1:
        status = "pass"
        note = f"单一 primary 协议：{next(iter(primary))}"
    else:
        status = "fail"
        note = f"混用多个 primary 协议：{sorted(primary)}"
    return [
        _row(
            check_id=check["id"],
            check_type="protocol_consistency",
            scope="poses",
            metric_name="primary_protocol_count",
            metric_value=float(len(primary)),
            threshold=1.0,
            comparator="eq",
            status=status,
            source="poses",
            note=note,
        )
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    *,
    check_id: str,
    check_type: str,
    scope: str,
    metric_name: str,
    metric_value: float | None,
    threshold: float | None,
    comparator: str,
    status: str,
    source: str,
    note: str,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "check_type": check_type,
        "scope": scope,
        "metric_name": metric_name,
        "metric_value": "" if metric_value is None else metric_value,
        "threshold": "" if threshold is None else threshold,
        "comparator": comparator,
        "status": status,
        "source": source,
        "note": note,
    }


def _uncertainty(
    uncertainty_id: str,
    *,
    scope: str,
    kind: str,
    statement: str,
    impact: str,
    resolves_with: str,
) -> dict[str, Any]:
    return {
        "uncertainty_id": uncertainty_id,
        "scope": scope,
        "kind": kind,
        "statement": statement,
        "impact": impact,
        "resolves_with": resolves_with,
        "status": "open",
    }


def _resolution_hint(check_type: str) -> str:
    return {
        "redock_rmsd": "用共晶配体重对接，RMSD 需 ≤ 2 A 才谈得上姿势可信",
        "control_ordering": "加入已知效价梯度的对照药，或改用同类内相对排序并人工复核",
        "mutation_trend": "补已知耐药位点的实验倍数变化，校准 panel 方向",
        "method_agreement": "统一刚性/柔性协议，或只保留一种并声明其局限",
    }.get(check_type, "补参考数据或人工复核")


def _aggregate_status(
    required_failures: list[str],
    ranking_warnings: list[str],
    required_gaps: list[str],
) -> str:
    if required_failures:
        return "fail"
    if ranking_warnings:
        return "warn"
    if required_gaps:
        return "gap"
    return "pass"


def _summary_text(rows: list[dict[str, Any]], status: str) -> str:
    counts = {name: 0 for name in ("pass", "fail", "gap", "not_applicable")}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    detail = " / ".join(f"{key}={value}" for key, value in counts.items() if value)
    label = {
        "pass": "参考对照通过",
        "warn": "排序参考未通过",
        "fail": "姿势参考失败",
        "gap": "参考数据缺口",
        "not_evaluated": "未评估",
    }[status]
    return f"{label}（{detail}）"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-12 or dy < 1e-12:
        return None
    return num / (dx * dy)
