"""Shared fixture builder for stage-3 tests."""

from __future__ import annotations

import csv
import json
from pathlib import Path

CANDIDATES = [
    "candidate_id,name,role,smiles,source,chemotype,heavy_atoms,ligand_sdf_path,ligand_pdbqt_path,availability,synthesizability,solubility_risk,selectivity_risk,notes",
    "c1,C1,candidate,,fixture,sulfonamide,30,,,0.8,0.8,0.2,0.2,",
    "c2,C2,candidate,,fixture,peptidomimetic,35,,,0.8,0.8,0.2,0.2,",
    "ctrl,CTRL,control,,fixture,drug,40,,,,,,,control ligand",
]

RECEPTORS = [
    "receptor_id,label,mutations,pdb_path,pdbqt_path,flex_pdbqt_path,box_center_x,box_center_y,box_center_z,box_size_x,box_size_y,box_size_z,is_primary",
    "WT,WT,,,rec.pdbqt,,,,,20,20,20,true",
    "M1,M1,M1,,rec.pdbqt,,,,,20,20,20,false",
]

POSES = [
    "candidate_id,receptor_id,pose_index,protocol,protocol_role,engine,affinity_kcal_mol,heavy_atoms,ligand_efficiency,centroid_distance_to_crystal_A,site_overlap_fraction,min_distance_to_Asp25_oxygens,asp25_contacts_under_3.5A,clash_pairs,polar_contacts,geometry_source,confidence_score,confidence_type",
    "c1,WT,1,vina_flex,primary,replay,-8.500,30,0.283,1.200,0.900,2.800,3,0,2,table,,",
    "c1,WT,2,vina_flex,primary,replay,-8.200,30,0.273,1.800,0.700,3.100,1,0,1,table,,",
    "c1,M1,1,vina_flex,primary,replay,-7.800,30,0.260,1.500,0.850,2.900,3,0,2,table,,",
    "c2,WT,1,vina_flex,primary,replay,-7.600,35,0.217,2.000,0.820,2.900,2,0,2,table,,",
    "c2,M1,1,vina_flex,primary,replay,-6.900,35,0.197,2.400,0.600,3.400,0,0,1,table,,",
    "ctrl,WT,1,vina_flex,primary,replay,-9.000,40,0.225,0.800,0.950,2.700,4,0,3,table,,",
    "ctrl,M1,1,vina_flex,primary,replay,-8.800,40,0.220,0.900,0.940,2.750,4,0,3,table,,",
]

PANEL = [
    "candidate_id,receptor_id,mutation,rigid_delta,flex_delta,pose_changed,known_resistance",
    "c1,M1,M1,0.200,0.300,false,false",
    "c2,M1,M1,0.100,0.900,true,false",
    "ctrl,M1,M1,0.150,0.250,false,true",
]


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def build_case(
    root: Path,
    *,
    reference_rmsd: float = 1.2,
    expected_order: tuple[str, str] = ("ctrl", "c1"),
    engine: str = "replay",
) -> Path:
    """Create a tiny case tree and return the config path."""
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)

    _write(inputs / "candidates.csv", CANDIDATES)
    _write(inputs / "receptors.csv", RECEPTORS)
    _write(inputs / "poses.csv", POSES)
    _write(inputs / "panel.csv", PANEL)
    _write(
        inputs / "reference_poses.csv",
        [
            "candidate_id,receptor_id,pose_index,rmsd_to_reference_A,reference_conformer,affinity_kcal_mol",
            f"ctrl,WT,1,{reference_rmsd},A,-9.000",
            f"ctrl,WT,2,{reference_rmsd + 0.5},B,-8.800",
        ],
    )

    if engine == "af3":
        _write(
            inputs / "af3_manifest.csv",
            [
                "candidate_id,receptor_id,structure_path,ranking_score,iptm,ptm,chain_pair_iptm,notes",
                "c1,WT,inputs/model_c1.pdb,0.82,0.75,0.70,0.60,",
                "c2,WT,missing/does_not_exist.pdb,0.61,0.60,0.55,0.40,",
            ],
        )
        (inputs / "model_c1.pdb").write_text(_pdb_atom(10.0, 10.0, 10.0), encoding="utf-8")

    config = {
        "schema_version": "stage3-config/1",
        "case_id": "tiny",
        "target": {"name": "toy target"},
        "inputs": {
            "candidates": "inputs/candidates.csv",
            "receptors": "inputs/receptors.csv",
            "poses": "inputs/poses.csv",
            "panel": "inputs/panel.csv",
            "reference_poses": "inputs/reference_poses.csv",
        },
        "engine": {
            "name": engine,
            "options": {"manifest": "inputs/af3_manifest.csv"} if engine == "af3" else {},
        },
        "reference": {
            "checks": [
                {
                    "id": "redock",
                    "type": "redock_rmsd",
                    "candidate_id": "ctrl",
                    "receptor_id": "WT",
                    "threshold": 2.0,
                    "required": True,
                    "impact": "blocks_advance",
                },
                {
                    "id": "order",
                    "type": "control_ordering",
                    "receptor_id": "WT",
                    "expected": list(expected_order),
                    "required": True,
                    "impact": "limits_ranking",
                },
                {
                    "id": "trend",
                    "type": "mutation_trend",
                    "candidate_id": "ctrl",
                    "threshold": 0.1,
                    "required": False,
                    "impact": "limits_ranking",
                },
            ]
        },
        "criteria": {
            "primary_receptor": "WT",
            "min_site_overlap": 0.70,
            "min_catalytic_contacts": 1,
            "max_clashes": 0,
            "max_affinity_kcal_mol": -7.0,
            "min_ligand_efficiency": 0.18,
            "max_flex_delta": 1.0,
        },
        "runs_dir": "runs",
    }
    if engine == "af3":
        config["inputs"].pop("poses")
    config_path = root / "case" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _pdb_atom(x: float, y: float, z: float) -> str:
    line = (
        f"ATOM  {1:>5} {'CA':<4}{' '}{'ALA':>3} {'A'}{1:>4}    "
        f"{x:>8.3f}{y:>8.3f}{z:>8.3f}  1.00  0.00           C\n"
    )
    return line


def read_output(run_dir: Path, name: str) -> list[dict[str, str]]:
    path = run_dir / "outputs" / name
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
