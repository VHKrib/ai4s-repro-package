"""Human-readable stage-3 report.

The report is generated from the same tables that are written to disk, so a
reader can always trace a sentence back to a CSV row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .applicability import ApplicabilityResult
from .config import Config
from .decide import DecisionResult
from .intake import IntakeResult
from .io_utils import format_seconds
from .qc import QCResult
from .rank import RankResult


def write_report(
    path: Path,
    *,
    run_id: str,
    config: Config,
    intake: IntakeResult,
    applicability: ApplicabilityResult,
    qc: QCResult,
    rank: RankResult,
    decisions: DecisionResult,
    elapsed: float,
    artifacts: dict[str, str],
) -> None:
    lines: list[str] = []
    lines.append(f"# 阶段 03：计算建模、评估与排序 — {config.case_id}")
    lines.append("")
    target = config.target.get("name") or "未命名靶点"
    lines.append(
        f"靶点：{target} ｜ 引擎：{config.engine} ｜ 主受体：{intake.primary_receptor} "
        f"｜ 运行：`{run_id}` ｜ 用时：{format_seconds(elapsed)}"
    )
    if config.engine == "replay":
        lines.append(
            "引擎说明：replay 只读取规范化后的既有对接结果，不重新计算；"
            "可复现性由输入哈希保证，方法本身是 Vina + 柔性突变侧链。"
        )
    lines.append("")
    lines.append(_summary_line(decisions))
    lines.append("")

    lines.append("## 1. 方法适用性与参考对照")
    lines.append("")
    if applicability.rows:
        lines.append(
            _table(
                ["检查", "对象", "指标", "数值", "阈值", "结果", "说明"],
                [
                    [
                        row["check_id"],
                        row["scope"],
                        row["metric_name"],
                        _fmt(row["metric_value"]),
                        _fmt(row["threshold"]),
                        _STATUS_LABELS.get(row["status"], row["status"]),
                        row["note"],
                    ]
                    for row in applicability.rows
                ],
            )
        )
    else:
        lines.append("未声明参考对照检查。")
    lines.append("")
    lines.append(_reference_interpretation(applicability))
    lines.append("")

    lines.append("## 2. 候选姿势与复合物")
    lines.append("")
    lines.append(
        _table(
            ["候选", "角色", "主受体", "引擎", "协议", "对接分", "配体效率", "位点重叠", "催化接触", "位点重叠标准差", "姿势选择"],
            [
                [
                    score["name"],
                    score["role"],
                    score["primary_receptor"],
                    score["engine"],
                    _pose_protocol(qc, score["candidate_id"], intake.primary_receptor),
                    _fmt(score["affinity_kcal_mol"]),
                    _fmt(score["ligand_efficiency"]),
                    _fmt(_pose_field(qc, score["candidate_id"], intake.primary_receptor, "site_overlap_fraction")),
                    _int(_pose_field(qc, score["candidate_id"], intake.primary_receptor, "asp25_contacts_under_3.5A")),
                    _fmt(qc.consensus.get((score["candidate_id"], intake.primary_receptor), {}).get("overlap_std")),
                    _pose_reason(qc, score["candidate_id"], intake.primary_receptor),
                ]
                for score in rank.scores
            ],
        )
    )
    lines.append("")
    lines.append(
        "对接分是打分函数输出，不是实测亲和力；位点重叠与催化接触是几何证据，"
        "只能说明姿势落在预期口袋内，不能证明功能被抑制。"
    )
    lines.append("")

    lines.append("## 3. 综合评估与排序")
    lines.append("")
    weights = config.weights
    lines.append(
        "综合分 = "
        + " + ".join(f"{_WEIGHT_LABELS[name]}×{value:.2f}" for name, value in weights.items())
        + "。各分量都是固定窗口下的 0–1 变换，不随本批候选重新归一化。"
    )
    lines.append("")
    ranked = [row for row in decisions.ranked_rows if row["role"] == "candidate"]
    lines.append(
        _table(
            ["排名", "候选", "分层", "综合分", "姿势", "亲和力", "一致性", "成药性", "耐药", "决策"],
            [
                [
                    str(row["rank"]),
                    row["name"],
                    row["tier"] or "-",
                    _fmt(row["composite_score"]),
                    _fmt(row["pose_score"]),
                    _fmt(row["affinity_score"]),
                    _fmt(row["consistency_score"]),
                    _fmt(row["developability_score"]),
                    _fmt(row["resistance_score"]),
                    _DECISION_LABELS[row["decision"]],
                ]
                for row in ranked
            ],
        )
    )
    lines.append("")
    controls = [row for row in decisions.ranked_rows if row["role"] != "candidate"]
    if controls:
        lines.append(
            "对照条目（不参与排名）："
            + "；".join(
                f"{row['name']} 综合分 {_fmt(row['composite_score'])}（{row['decision_codes'] or '无标记'}）"
                for row in controls
            )
        )
        lines.append("")

    lines.append("## 4. 突变负担")
    lines.append("")
    matrix_rows = [row for row in qc.matrix_rows if row["mutation"] and row["risk_level"] != "none"]
    if matrix_rows:
        lines.append(
            _table(
                ["候选", "受体", "突变", "已知耐药", "相对 WT Δ", "位点重叠", "姿势改变", "风险", "标记"],
                [
                    [
                        row["candidate_id"],
                        row["receptor_id"],
                        row["mutation"],
                        _bool(row["known_resistance"]),
                        _fmt(row["delta_vs_wt"]),
                        _fmt(row["site_overlap_fraction"]),
                        _bool(row["pose_changed"]),
                        _RISK_LABELS.get(row["risk_level"], row["risk_level"]),
                        (row["flags"] or "").replace(";", ", "),
                    ]
                    for row in sorted(
                        matrix_rows,
                        key=lambda row: (_RISK_ORDER.get(row["risk_level"], 9), row["candidate_id"], row["receptor_id"]),
                    )
                ],
            )
        )
    else:
        lines.append("没有出现需要标记的突变风险。")
    lines.append("")
    lines.append(
        "风险分级只看计算差值：known_resistance 来自输入标注，不是本 agent 的判断；"
        "柔性侧链 panel 能提示方向，但不能当耐药排序或 go/no-go 门槛。"
    )
    lines.append("")

    lines.append("## 5. 判据与决策")
    lines.append("")
    lines.append(
        _table(
            ["候选", "决策", "依据", "下一环节", "需人工复核"],
            [
                [
                    row["name"],
                    _DECISION_LABELS[row["decision"]],
                    row["decision_reason"],
                    row["next_stage"] or "-",
                    _bool(row["reviewer_required"]),
                ]
                for row in decisions.decisions
            ],
        )
    )
    lines.append("")

    lines.append("## 6. 未解决的不确定性")
    lines.append("")
    if decisions.uncertainties:
        lines.append(
            _table(
                ["ID", "范围", "类型", "说明", "影响", "如何解决"],
                [
                    [
                        row["uncertainty_id"],
                        row["scope"],
                        row["kind"],
                        row["statement"],
                        _IMPACT_LABELS.get(row["impact"], row["impact"]),
                        row["resolves_with"],
                    ]
                    for row in decisions.uncertainties
                ],
            )
        )
    else:
        lines.append("无。")
    lines.append("")

    lines.append("## 7. 判据与参数")
    lines.append("")
    lines.append(
        _table(
            ["参数", "值"],
            [[key, _param(value)] for key, value in config.criteria.items()]
            + [[f"权重 · {_WEIGHT_LABELS[name]}", f"{value:.2f}"] for name, value in config.weights.items()]
            + [["分层阈值 A / B", f"{config.tiers['A']:.2f} / {config.tiers['B']:.2f}"]],
        )
    )
    lines.append("")

    lines.append("## 8. 输出与可复现性")
    lines.append("")
    lines.append(_table(["文件", "路径"], [[name, f"`{value}`"] for name, value in artifacts.items()]))
    lines.append("")
    lines.append(
        "每次运行都会写出 manifest.json，记录输入文件哈希、规范化表哈希、参数、"
        "软件版本与随机种子；相同输入重跑得到相同的表格字节。"
    )
    lines.append("")

    lines.append("## 9. 解释边界")
    lines.append("")
    for item in (
        "AF3 输出是静态复合物，多个预测样本不是运动轨迹；置信度不能替代结合自由能。",
        "对接评分不等于实测亲和力，跨体系数值不可直接比较。",
        "氢键与盐桥是模型中的几何接近证据，未做角度与质子化校验。",
        "结合姿势合理不等于功能受抑制，最终需要实验验证。",
        "MD 只补充动态证据，不替代实验；本阶段不把 MD 结果当判据。",
    ):
        lines.append(f"- {item}")
    lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def _summary_line(decisions: DecisionResult) -> str:
    counts: dict[str, int] = {}
    for row in decisions.decisions:
        if row["role"] != "candidate":
            continue
        counts[row["decision"]] = counts.get(row["decision"], 0) + 1
    parts = "，".join(
        f"{_DECISION_LABELS.get(name, name)} {count}" for name, count in sorted(counts.items())
    )
    top = [row for row in decisions.ranked_rows if row["role"] == "candidate" and row["rank"] == 1]
    if top:
        return f"候选 {sum(counts.values())} 个：{parts}。排名第一：{top[0]['name']}（综合分 {_fmt(top[0]['composite_score'])}）。"
    return f"无可排名候选：{parts}。"


def _reference_interpretation(applicability: ApplicabilityResult) -> str:
    if applicability.status == "pass":
        return "参考对照全部通过，排序结果可在本体系内相对比较；仍需实验校准绝对值。"
    if applicability.status == "fail":
        return "姿势参考（红对接）未通过。按预设判据，本轮排序不能直接用于推进候选，决策被降级为人工复核。"
    if applicability.status == "warn":
        return (
            "姿势参考通过，但排序参考未通过（已知药物次序或突变趋势复现不了）。"
            "候选的姿势可信，排序只应在同一化学系列内作相对参考，并带风险标记进入实验。"
        )
    if applicability.status == "gap":
        return "参考数据不完整，只能记录校验缺口；排序结果仅作相对参考。"
    return "未声明参考对照，无法判断方法是否适用于本体系。"


def _pose_field(qc: QCResult, candidate_id: str, receptor_id: str, field: str) -> Any:
    for row in qc.pose_rows:
        if (
            row["candidate_id"] == candidate_id
            and row["receptor_id"] == receptor_id
            and row["selected"]
        ):
            return row.get(field)
    return None


def _pose_reason(qc: QCResult, candidate_id: str, receptor_id: str) -> str:
    for row in qc.pose_rows:
        if (
            row["candidate_id"] == candidate_id
            and row["receptor_id"] == receptor_id
            and row["selected"]
        ):
            return f"pose {row['pose_index']} · {row['selection_reason']}"
    return "-"


def _pose_protocol(qc: QCResult, candidate_id: str, receptor_id: str) -> str:
    for row in qc.pose_rows:
        if (
            row["candidate_id"] == candidate_id
            and row["receptor_id"] == receptor_id
            and row["selected"]
        ):
            return row["protocol"]
    return "-"


def _int(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    if isinstance(value, str):
        try:
            return f"{float(value):.2f}"
        except ValueError:
            return value
    return str(value)


def _bool(value: Any) -> str:
    if value in ("true", True):
        return "是"
    if value in ("false", False):
        return "否"
    return "-"


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    return _fmt(value)


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    headers = list(headers)
    lines = ["| " + " | ".join(_escape(h) for h in headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_escape(cell) for cell in row) + " |")
    return "\n".join(lines)


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


_STATUS_LABELS = {"pass": "通过", "fail": "失败", "gap": "缺口", "not_applicable": "不适用"}
_DECISION_LABELS = {
    "advance": "进入实验",
    "advance_with_flags": "带标记推进",
    "revise": "返回重新设计",
    "needs_review": "人工复核",
    "control_only": "仅对照",
    "reject": "删除",
}
_RISK_LABELS = {"none": "无", "low": "低", "moderate": "中", "high": "高", "unknown": "未评估"}
_IMPACT_LABELS = {
    "blocks_advance": "阻止自动放行",
    "limits_ranking": "限制排序解释",
    "note": "提示",
}
_WEIGHT_LABELS = {
    "pose": "姿势",
    "affinity": "亲和力",
    "consistency": "一致性",
    "developability": "成药性",
    "resistance": "耐药",
}
_RISK_ORDER = {"high": 0, "moderate": 1, "low": 2, "unknown": 3, "none": 4}
