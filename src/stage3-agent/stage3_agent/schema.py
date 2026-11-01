"""Single source of truth for every table stage 3 reads or writes.

Two rules keep the pipeline stable:

1. Column order and names are declared here, never inferred from data.
2. Every row is validated before it is written. A drifted column set is a hard
   error, not a silently different CSV.

`json_schema()` exports the same contract as draft-07 JSON Schema so an
external lab system can validate the outputs without importing this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .errors import ContractError

FLOAT_DECIMALS = 3


@dataclass(frozen=True)
class Col:
    name: str
    dtype: str = "str"  # str | int | float | bool | enum
    values: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = True
    note: str = ""


@dataclass(frozen=True)
class TableSpec:
    name: str
    description: str
    columns: tuple[Col, ...]
    key: tuple[str, ...] = ()

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Col:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)


def _cols(*specs: tuple) -> tuple[Col, ...]:
    return tuple(Col(*spec) for spec in specs)


# ---------------------------------------------------------------------------
# Normalized inputs (stage 3 only ever reads these, never the raw exports)
# ---------------------------------------------------------------------------

CANDIDATES = TableSpec(
    "candidates",
    "候选/对照清单。role=candidate 参与排序，control 只做方法对照。",
    _cols(
        ("candidate_id", "str", (), None, None, False, "项目内唯一 ID"),
        ("name", "str", (), None, None, False),
        ("role", "enum", ("candidate", "control", "reference"), None, None, False),
        ("smiles", "str"),
        ("source", "str"),
        ("chemotype", "str"),
        ("heavy_atoms", "int", (), 1, None),
        ("ligand_sdf_path", "str"),
        ("ligand_pdbqt_path", "str"),
        ("availability", "float", (), 0, 1),
        ("synthesizability", "float", (), 0, 1),
        ("solubility_risk", "float", (), 0, 1),
        ("selectivity_risk", "float", (), 0, 1),
        ("notes", "str"),
    ),
    key=("candidate_id",),
)

RECEPTORS = TableSpec(
    "receptors",
    "受体/复合物定义。replay 引擎只要求 ID；vina 引擎要求 pdbqt 与对接盒。",
    _cols(
        ("receptor_id", "str", (), None, None, False),
        ("label", "str"),
        ("mutations", "str", (), None, None, True, "分号分隔，WT 留空"),
        ("pdb_path", "str"),
        ("pdbqt_path", "str"),
        ("flex_pdbqt_path", "str"),
        ("box_center_x", "float"),
        ("box_center_y", "float"),
        ("box_center_z", "float"),
        ("box_size_x", "float", (), 1, None),
        ("box_size_y", "float", (), 1, None),
        ("box_size_z", "float", (), 1, None),
        ("is_primary", "bool"),
    ),
    key=("receptor_id",),
)

POSES = TableSpec(
    "poses",
    "tidy 姿势表：一行一个姿势。engine=replay 时由外部结果转换而来。",
    _cols(
        ("candidate_id", "str", (), None, None, False),
        ("receptor_id", "str", (), None, None, False),
        ("pose_index", "int", (), 1, None, False),
        ("protocol", "str", (), None, None, False, "如 vina_flex_sidechain"),
        ("protocol_role", "enum", ("primary", "sensitivity", "reference"), None, None, False),
        ("engine", "enum", ("vina", "af3", "replay", "external"), None, None, False),
        ("affinity_kcal_mol", "float"),
        ("heavy_atoms", "int", (), 1, None),
        ("ligand_efficiency", "float", (), 0, None),
        ("centroid_distance_to_crystal_A", "float", (), 0, None),
        ("site_overlap_fraction", "float", (), 0, 1),
        ("min_distance_to_Asp25_oxygens", "float", (), 0, None),
        ("asp25_contacts_under_3.5A", "int", (), 0, None),
        ("clash_pairs", "int", (), 0, None),
        ("polar_contacts", "int", (), 0, None),
        ("geometry_source", "enum", ("table", "sdf", "af3_confidence", "none"), None, None, False),
        ("confidence_score", "float", (), 0, 1),
        ("confidence_type", "enum", ("ranking_score", "iptm", "ptm"), None, None, True),
    ),
    key=("candidate_id", "receptor_id", "protocol", "pose_index"),
)

PANEL = TableSpec(
    "panel",
    "突变 panel：每个候选在每个受体背景下的刚性/柔性侧链对接差值。",
    _cols(
        ("candidate_id", "str", (), None, None, False),
        ("receptor_id", "str", (), None, None, False),
        ("mutation", "str", (), None, None, False),
        ("rigid_delta", "float"),
        ("flex_delta", "float"),
        ("pose_changed", "bool"),
        ("known_resistance", "bool"),
    ),
    key=("candidate_id", "receptor_id"),
)

REFERENCE_POSES = TableSpec(
    "reference_poses",
    "参考配体重对接姿势，用于方法适用性检查。",
    _cols(
        ("candidate_id", "str", (), None, None, False),
        ("receptor_id", "str", (), None, None, False),
        ("pose_index", "int", (), 1, None, False),
        ("rmsd_to_reference_A", "float", (), 0, None, False),
        ("reference_conformer", "str"),
        ("affinity_kcal_mol", "float"),
    ),
    key=("candidate_id", "receptor_id", "pose_index"),
)

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

POSE_METRICS = TableSpec(
    "pose_metrics",
    "每个姿势的几何指标与选中标记。",
    _cols(
        ("candidate_id", "str", (), None, None, False),
        ("receptor_id", "str", (), None, None, False),
        ("protocol", "str", (), None, None, False),
        ("protocol_role", "enum", ("primary", "sensitivity", "reference"), None, None, False),
        ("engine", "enum", ("vina", "af3", "replay", "external"), None, None, False),
        ("pose_index", "int", (), 1, None, False),
        ("affinity_kcal_mol", "float"),
        ("heavy_atoms", "int", (), 1, None),
        ("ligand_efficiency", "float", (), 0, None),
        ("centroid_distance_to_crystal_A", "float", (), 0, None),
        ("site_overlap_fraction", "float", (), 0, 1),
        ("min_distance_to_Asp25_oxygens", "float", (), 0, None),
        ("asp25_contacts_under_3.5A", "int", (), 0, None),
        ("clash_pairs", "int", (), 0, None),
        ("polar_contacts", "int", (), 0, None),
        ("pose_score", "float", (), 0, 1),
        ("selected", "bool", (), None, None, False),
        ("selection_reason", "str"),
        ("geometry_source", "enum", ("table", "sdf", "af3_confidence", "none"), None, None, False),
        ("confidence_score", "float", (), 0, 1),
        ("confidence_type", "enum", ("ranking_score", "iptm", "ptm"), None, None, True),
    ),
    key=("candidate_id", "receptor_id", "protocol", "pose_index"),
)

RECEPTOR_MATRIX = TableSpec(
    "receptor_matrix",
    "候选 × 受体背景矩阵：结合、几何与突变风险。",
    _cols(
        ("candidate_id", "str", (), None, None, False),
        ("receptor_id", "str", (), None, None, False),
        ("mutation", "str"),
        ("known_resistance", "bool"),
        ("affinity_kcal_mol", "float"),
        ("delta_vs_wt", "float"),
        ("site_overlap_fraction", "float", (), 0, 1),
        ("catalytic_contacts", "int", (), 0, None),
        ("pose_changed", "bool"),
        ("risk_level", "enum", ("none", "low", "moderate", "high", "unknown"), None, None, False),
        ("flags", "str"),
    ),
    key=("candidate_id", "receptor_id"),
)

REFERENCE_CHECK = TableSpec(
    "reference_check",
    "方法适用性与参考对照检查。gap 表示无参考可比，不是通过。",
    _cols(
        ("check_id", "str", (), None, None, False),
        ("check_type", "str", (), None, None, False),
        ("scope", "str"),
        ("metric_name", "str"),
        ("metric_value", "float"),
        ("threshold", "float"),
        ("comparator", "enum", ("le", "ge", "eq", "none"), None, None, False),
        ("status", "enum", ("pass", "fail", "gap", "not_applicable"), None, None, False),
        ("source", "str"),
        ("note", "str"),
    ),
    key=("check_id",),
)

CANDIDATES_RANKED = TableSpec(
    "candidates_ranked",
    "候选排序表。对照行保留但不参与排名。",
    _cols(
        ("rank", "int", (), 1, None),
        ("candidate_id", "str", (), None, None, False),
        ("name", "str", (), None, None, False),
        ("role", "enum", ("candidate", "control", "reference"), None, None, False),
        ("tier", "enum", ("A", "B", "C"), None, None, True),
        ("decision", "enum", ("advance", "advance_with_flags", "revise", "needs_review", "control_only", "reject"), None, None, False),
        ("composite_score", "float", (), 0, 1),
        ("pose_score", "float", (), 0, 1),
        ("affinity_score", "float", (), 0, 1),
        ("consistency_score", "float", (), 0, 1),
        ("developability_score", "float", (), 0, 1),
        ("resistance_score", "float", (), 0, 1),
        ("affinity_kcal_mol", "float"),
        ("ligand_efficiency", "float", (), 0, None),
        ("primary_receptor", "str"),
        ("engine", "enum", ("vina", "af3", "replay", "external"), None, None, False),
        ("uncertainty_count", "int", (), 0, None, False),
        ("decision_codes", "str"),
    ),
    key=("candidate_id",),
)

DECISIONS = TableSpec(
    "decisions",
    "判据结果。失败原因必须保留，供阶段 02 重新设计时引用。",
    _cols(
        ("candidate_id", "str", (), None, None, False),
        ("name", "str", (), None, None, False),
        ("role", "enum", ("candidate", "control", "reference"), None, None, False),
        ("tier", "enum", ("A", "B", "C"), None, None, True),
        ("composite_score", "float", (), 0, 1),
        ("decision", "enum", ("advance", "advance_with_flags", "revise", "needs_review", "control_only", "reject"), None, None, False),
        ("decision_codes", "str"),
        ("decision_reason", "str"),
        ("next_stage", "enum", ("02", "04", "review", ""), None, None, True),
        ("reviewer_required", "bool", (), None, None, False),
        ("hard_failures", "str"),
        ("soft_flags", "str"),
    ),
    key=("candidate_id",),
)

UNCERTAINTIES = TableSpec(
    "uncertainties",
    "未解决的不确定性。impact=blocks_advance 的项目不能被自动放行。",
    _cols(
        ("uncertainty_id", "str", (), None, None, False),
        ("scope", "str", (), None, None, False),
        ("kind", "str", (), None, None, False),
        ("statement", "str", (), None, None, False),
        ("impact", "enum", ("blocks_advance", "limits_ranking", "note"), None, None, False),
        ("resolves_with", "str"),
        ("status", "enum", ("open", "closed"), None, None, False),
    ),
    key=("uncertainty_id",),
)

INPUT_TABLES = {
    spec.name: spec for spec in (CANDIDATES, RECEPTORS, POSES, PANEL, REFERENCE_POSES)
}

OUTPUT_TABLES = {
    spec.name: spec
    for spec in (
        POSE_METRICS,
        RECEPTOR_MATRIX,
        REFERENCE_CHECK,
        CANDIDATES_RANKED,
        DECISIONS,
        UNCERTAINTIES,
    )
}


# ---------------------------------------------------------------------------
# Coercion / validation
# ---------------------------------------------------------------------------


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def coerce_value(col: Col, value: Any) -> str:
    """Convert one cell to its canonical string form, or raise ContractError."""
    if _is_blank(value):
        if col.nullable:
            return ""
        raise ContractError(f"column {col.name}: value required")

    if col.dtype == "str":
        text = str(value).strip().replace("\r", " ").replace("\n", " ")
        return " ".join(text.split())

    if col.dtype == "int":
        try:
            number = int(float(str(value).strip()))
        except (TypeError, ValueError) as exc:
            raise ContractError(f"column {col.name}: not an int ({value!r})") from exc
        if col.minimum is not None and number < col.minimum:
            raise ContractError(f"column {col.name}: {number} < {col.minimum}")
        if col.maximum is not None and number > col.maximum:
            raise ContractError(f"column {col.name}: {number} > {col.maximum}")
        return str(number)

    if col.dtype == "float":
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ContractError(f"column {col.name}: not a float ({value!r})") from exc
        if col.minimum is not None and number < col.minimum - 1e-9:
            raise ContractError(f"column {col.name}: {number} < {col.minimum}")
        if col.maximum is not None and number > col.maximum + 1e-9:
            raise ContractError(f"column {col.name}: {number} > {col.maximum}")
        if abs(number) < 0.0005:
            number = 0.0
        return f"{number:.{FLOAT_DECIMALS}f}"

    if col.dtype == "bool":
        text = str(value).strip().lower()
        if text in ("true", "1", "yes", "y", "pass"):
            return "true"
        if text in ("false", "0", "no", "n", "fail"):
            return "false"
        raise ContractError(f"column {col.name}: not a bool ({value!r})")

    if col.dtype == "enum":
        text = str(value).strip()
        if text not in col.values:
            raise ContractError(
                f"column {col.name}: {text!r} not in {list(col.values)}"
            )
        return text

    raise ContractError(f"column {col.name}: unknown dtype {col.dtype}")


def normalize_row(spec: TableSpec, row: dict[str, Any]) -> dict[str, str]:
    missing = [name for name in spec.column_names if name not in row]
    extra = [name for name in row if name not in spec.column_names]
    if missing or extra:
        raise ContractError(
            f"table {spec.name}: missing columns {missing}, unexpected columns {extra}"
        )
    return {col.name: coerce_value(col, row[col.name]) for col in spec.columns}


def normalize_rows(spec: TableSpec, rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    return [normalize_row(spec, row) for row in rows]


def validate_columns(spec: TableSpec, fieldnames: Iterable[str]) -> None:
    actual = list(fieldnames)
    expected = spec.column_names
    if actual != expected:
        missing = [c for c in expected if c not in actual]
        extra = [c for c in actual if c not in expected]
        raise ContractError(
            f"table {spec.name}: column contract broken. "
            f"missing={missing} extra={extra} expected_order={expected}"
        )


def json_schema(spec: TableSpec) -> dict[str, Any]:
    """Draft-07 schema for one table (rows are objects, numbers as numbers)."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for col in spec.columns:
        if not col.nullable:
            required.append(col.name)
        if col.dtype == "int":
            schema: dict[str, Any] = {"type": ["integer", "null"]}
        elif col.dtype == "float":
            schema = {"type": ["number", "null"]}
        elif col.dtype == "bool":
            schema = {"type": ["boolean", "null"]}
        elif col.dtype == "enum":
            schema = {"enum": list(col.values) + ([] if not col.nullable else [None])}
        else:
            schema = {"type": ["string", "null"]}
        if col.dtype in ("int", "float"):
            if col.minimum is not None:
                schema["minimum"] = col.minimum
            if col.maximum is not None:
                schema["maximum"] = col.maximum
        if col.note:
            schema["description"] = col.note
        properties[col.name] = schema
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": spec.name,
        "description": spec.description,
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": properties,
        },
    }


def all_schemas() -> dict[str, dict[str, Any]]:
    return {name: json_schema(spec) for name, spec in {**INPUT_TABLES, **OUTPUT_TABLES}.items()}
