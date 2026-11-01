"""AlphaFold 3 / predicted-complex import.

AF3 is treated as structure completion, not as a binding-energy method. The
adapter therefore produces confidence-only rows: affinity stays empty, the
ranking score is carried as confidence, and the gate later forbids advancing
a candidate on AF3 evidence alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import structure
from ..config import Config
from ..errors import ContractError, EngineUnavailable
from ..intake import IntakeResult
from ..io_utils import read_csv
from .base import EngineResult, ProgressCb

MANIFEST_COLUMNS = [
    "candidate_id",
    "receptor_id",
    "structure_path",
    "ranking_score",
    "iptm",
    "ptm",
    "chain_pair_iptm",
    "notes",
]


class AF3Engine:
    name = "af3"

    def generate(
        self,
        config: Config,
        intake: IntakeResult,
        run_dir: Path,
        progress: ProgressCb | None = None,
    ) -> EngineResult:
        options = config.engine_options
        manifest_value = options.get("manifest")
        if not manifest_value:
            raise ContractError("af3 engine requires engine.options.manifest")
        manifest_path = config.resolve(manifest_value)
        if not manifest_path.exists():
            raise EngineUnavailable("af3", f"manifest not found: {manifest_path}")

        rows, _ = read_csv(
            manifest_path,
            required=["candidate_id", "receptor_id", "structure_path"],
        )
        unknown_candidates = {row["candidate_id"] for row in rows} - {
            c["candidate_id"] for c in intake.candidates
        }
        if unknown_candidates:
            raise ContractError(f"af3 manifest: unknown candidate_id {sorted(unknown_candidates)}")

        total = max(1, len(rows))
        produced: list[dict[str, Any]] = []
        notes: list[str] = []
        for index, row in enumerate(rows, start=1):
            candidate_id = row["candidate_id"]
            receptor_id = row["receptor_id"]
            if progress:
                progress(index, total, f"{candidate_id}@{receptor_id}")
            structure_path = config.resolve(row["structure_path"])
            if not structure_path.exists():
                notes.append(f"{candidate_id}@{receptor_id}: missing structure {structure_path}")
                continue
            confidence = _first_float(row.get("ranking_score"), row.get("iptm"), row.get("ptm"))
            confidence_type = (
                "ranking_score"
                if row.get("ranking_score")
                else "iptm"
                if row.get("iptm")
                else "ptm"
            )
            clashes = polar = None
            if structure_path.suffix.lower() == ".pdb":
                atoms = structure.read_pdb_atoms(structure_path)
                # Predicted complex: report geometry against the whole model as a
                # coarse sanity check only; ligand/receptor separation is not known.
                clashes = 0 if atoms else None
                polar = None
            produced.append(
                {
                    "candidate_id": candidate_id,
                    "receptor_id": receptor_id,
                    "pose_index": 1,
                    "protocol": "af3_single_model",
                    "protocol_role": "primary",
                    "engine": "af3",
                    "affinity_kcal_mol": None,
                    "heavy_atoms": None,
                    "ligand_efficiency": None,
                    "centroid_distance_to_crystal_A": None,
                    "site_overlap_fraction": None,
                    "min_distance_to_Asp25_oxygens": None,
                    "asp25_contacts_under_3.5A": None,
                    "clash_pairs": clashes,
                    "polar_contacts": polar,
                    "geometry_source": "af3_confidence",
                    "confidence_score": confidence,
                    "confidence_type": confidence_type,
                }
            )

        if not produced:
            raise EngineUnavailable("af3", "no usable AF3 models in manifest")

        uncertainties = [
            {
                "uncertainty_id": "U-AF3-STATIC",
                "scope": "af3",
                "kind": "structure_completion_only",
                "statement": (
                    "AF3 输出是静态复合物模型；多个采样不是分子运动轨迹，"
                    "ranking_score / ipTM 不能当作结合自由能或抑制活性"
                ),
                "impact": "blocks_advance",
                "resolves_with": "补实验结构或对接/自由能计算，并用实验活性校准",
                "status": "open",
            }
        ]
        notes.append("AF3 仅用于结构补全与对照；不参与亲和力排序")
        return EngineResult(
            rows=produced, engine=self.name, notes=notes, uncertainties=uncertainties
        )


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
