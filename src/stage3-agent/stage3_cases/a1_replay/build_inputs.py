#!/usr/bin/env python3
"""Convert the A1 docking results into the stage-3 input contract.

Source of truth: `struct_case/results/` produced by the docking scripts.
This script is the only place that knows the old column names; the stage-3
agent only ever sees `stage3_cases/a1_replay/*.csv`.

Regenerate with:
    python stage3_cases/a1_replay/build_inputs.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASE = Path(__file__).resolve().parent

LIGANDS = ["saquinavir", "darunavir", "3h", "34b"]
MUTANTS = ["D30N", "G48V", "I50V", "I54V", "L76V", "V82A", "I84V", "L90M"]

CANDIDATE_META = {
    "saquinavir": {
        "name": "Saquinavir",
        "role": "control",
        "smiles": "",
        "source": "已上市蛋白酶抑制剂；1HXB 共晶配体",
        "chemotype": "羟乙基胺拟肽",
        "heavy_atoms": "49",
        "availability": "1.000",
        "synthesizability": "1.000",
        "solubility_risk": "",
        "selectivity_risk": "",
        "notes": "红对接参照；SMILES 见 struct_case/ligand/saquinavir.sdf",
    },
    "darunavir": {
        "name": "Darunavir",
        "role": "control",
        "smiles": "",
        "source": "已上市蛋白酶抑制剂；A1_compounds 对照行",
        "chemotype": "羟乙基胺拟肽",
        "heavy_atoms": "38",
        "availability": "1.000",
        "synthesizability": "1.000",
        "solubility_risk": "",
        "selectivity_risk": "",
        "notes": "用于已知效价次序对照；SMILES 见 struct_case/ligand/darunavir.sdf",
    },
    "3h": {
        "name": "3h",
        "role": "candidate",
        "smiles": "",
        "source": "A1 / ChEMBL CHEMBL4444017",
        "chemotype": "羟乙基胺拟肽",
        "heavy_atoms": "41",
        "availability": "",
        "synthesizability": "",
        "solubility_risk": "",
        "selectivity_risk": "",
        "notes": "Ki 0.51 nM（摘要与 ChEMBL 一致）；成药性四项待补",
    },
    "34b": {
        "name": "34b",
        "role": "candidate",
        "smiles": "",
        "source": "A1 / ChEMBL CHEMBL5402748",
        "chemotype": "羟乙基胺拟肽",
        "heavy_atoms": "34",
        "availability": "",
        "synthesizability": "",
        "solubility_risk": "",
        "selectivity_risk": "",
        "notes": "摘要 0.32 nM vs ChEMBL 320 nM 单位矛盾未解决；成药性四项待补",
    },
}

BOX_CENTER = ("18.25", "-1.42", "10.21")
BOX_SIZE = ("24.0", "24.0", "24.0")

# saquinavir 没有单独命名的 ligand sdf；晶体 A 取向就是重对接参照。
LIGAND_SDF = {
    "saquinavir": "saquinavir_A.sdf",
    "darunavir": "darunavir.sdf",
    "3h": "3h.sdf",
    "34b": "34b.sdf",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def smiles_from_a1() -> dict[str, str]:
    rows = read_csv(ROOT / "a1_case" / "A1_compounds.csv")
    result = {}
    for row in rows:
        label = row.get("inferred_label", "")
        for ligand in ("3h", "34b"):
            if label.lower().startswith(ligand):
                result.setdefault(ligand, row.get("smiles", ""))
    return result


def build_candidates(smiles: dict[str, str]) -> None:
    columns = [
        "candidate_id",
        "name",
        "role",
        "smiles",
        "source",
        "chemotype",
        "heavy_atoms",
        "ligand_sdf_path",
        "ligand_pdbqt_path",
        "availability",
        "synthesizability",
        "solubility_risk",
        "selectivity_risk",
        "notes",
    ]
    rows = []
    for ligand in LIGANDS:
        meta = dict(CANDIDATE_META[ligand])
        meta["smiles"] = meta["smiles"] or smiles.get(ligand, "")
        meta["ligand_sdf_path"] = f"struct_case/ligand/{LIGAND_SDF[ligand]}"
        meta["ligand_pdbqt_path"] = f"struct_case/ligand/{ligand}.pdbqt"
        rows.append({"candidate_id": ligand, **meta})
    write_csv(CASE / "candidates.csv", columns, rows)


def build_receptors() -> None:
    columns = [
        "receptor_id",
        "label",
        "mutations",
        "pdb_path",
        "pdbqt_path",
        "flex_pdbqt_path",
        "box_center_x",
        "box_center_y",
        "box_center_z",
        "box_size_x",
        "box_size_y",
        "box_size_z",
        "is_primary",
    ]
    rows = [
        {
            "receptor_id": "WT",
            "label": "1HXB 野生型",
            "mutations": "",
            "pdb_path": "struct_case/receptor/1HXB_receptor.pdb",
            "pdbqt_path": "struct_case/receptor/1HXB_receptor.pdbqt",
            "flex_pdbqt_path": "struct_case/receptor/1HXB_receptor.pdbqt",
            "box_center_x": BOX_CENTER[0],
            "box_center_y": BOX_CENTER[1],
            "box_center_z": BOX_CENTER[2],
            "box_size_x": BOX_SIZE[0],
            "box_size_y": BOX_SIZE[1],
            "box_size_z": BOX_SIZE[2],
            "is_primary": "true",
        }
    ]
    for mutant in MUTANTS:
        flex_path = f"struct_case/mutants/{mutant}_flex_flex.pdbqt"
        # V82A 没有 flex_flex 制备；留空而不是指向另一种侧链处理。
        if not (ROOT / flex_path).exists():
            flex_path = ""
        rows.append(
            {
                "receptor_id": mutant,
                "label": mutant,
                "mutations": mutant,
                "pdb_path": f"struct_case/mutants/{mutant}.pdb",
                "pdbqt_path": f"struct_case/mutants/{mutant}_receptor.pdbqt",
                "flex_pdbqt_path": flex_path,
                "box_center_x": BOX_CENTER[0],
                "box_center_y": BOX_CENTER[1],
                "box_center_z": BOX_CENTER[2],
                "box_size_x": BOX_SIZE[0],
                "box_size_y": BOX_SIZE[1],
                "box_size_z": BOX_SIZE[2],
                "is_primary": "false",
            }
        )
    write_csv(CASE / "receptors.csv", columns, rows)


def build_poses() -> None:
    columns = [
        "candidate_id",
        "receptor_id",
        "pose_index",
        "protocol",
        "protocol_role",
        "engine",
        "affinity_kcal_mol",
        "heavy_atoms",
        "ligand_efficiency",
        "centroid_distance_to_crystal_A",
        "site_overlap_fraction",
        "min_distance_to_Asp25_oxygens",
        "asp25_contacts_under_3.5A",
        "clash_pairs",
        "polar_contacts",
        "geometry_source",
        "confidence_score",
        "confidence_type",
    ]
    rows: list[dict[str, str]] = []
    for receptor_id in ["WT"] + MUTANTS:
        for ligand in LIGANDS:
            source = ROOT / "struct_case" / "results" / f"panel_{ligand}_flex.csv"
            record = next(
                (row for row in read_csv(source) if row["receptor"] == receptor_id),
                None,
            )
            if record is None:
                continue
            rows.append(
                {
                    "candidate_id": ligand,
                    "receptor_id": receptor_id,
                    "pose_index": "1",
                    "protocol": "vina_flex_sidechain",
                    "protocol_role": "primary",
                    "engine": "replay",
                    "affinity_kcal_mol": record["affinity_kcal_mol"],
                    "heavy_atoms": record["heavy_atoms"],
                    "ligand_efficiency": record["ligand_efficiency"],
                    "centroid_distance_to_crystal_A": record["centroid_distance_to_crystal_A"],
                    "site_overlap_fraction": record["site_overlap_fraction"],
                    "min_distance_to_Asp25_oxygens": record["min_distance_to_Asp25_oxygens"],
                    "asp25_contacts_under_3.5A": record["asp25_contacts_under_3.5A"],
                    "clash_pairs": "",
                    "polar_contacts": "",
                    "geometry_source": "table",
                    "confidence_score": "",
                    "confidence_type": "",
                }
            )

    # The 9-pose WT table is kept as a sensitivity protocol: it shows the pose
    # spread behind the single best pose that the panel stored.
    for record in read_csv(ROOT / "struct_case" / "results" / "a1_docking.csv"):
        rows.append(
            {
                "candidate_id": record["ligand"],
                "receptor_id": "WT",
                "pose_index": record["pose"],
                "protocol": "vina_rigid_wt_9poses",
                "protocol_role": "sensitivity",
                "engine": "replay",
                "affinity_kcal_mol": record["affinity_kcal_mol"],
                "heavy_atoms": record["heavy_atoms"],
                "ligand_efficiency": record["ligand_efficiency"],
                "centroid_distance_to_crystal_A": record["centroid_distance_to_crystal_A"],
                "site_overlap_fraction": record["site_overlap_fraction"],
                "min_distance_to_Asp25_oxygens": record["min_distance_to_Asp25_oxygens"],
                "asp25_contacts_under_3.5A": record["asp25_contacts_under_3.5A"],
                "clash_pairs": "",
                "polar_contacts": "",
                "geometry_source": "table",
                "confidence_score": "",
                "confidence_type": "",
            }
        )
    write_csv(CASE / "poses.csv", columns, rows)


def build_panel() -> None:
    columns = [
        "candidate_id",
        "receptor_id",
        "mutation",
        "rigid_delta",
        "flex_delta",
        "pose_changed",
        "known_resistance",
    ]
    rows = []
    for record in read_csv(ROOT / "struct_case" / "results" / "panel_comparison.csv"):
        rows.append(
            {
                "candidate_id": record["ligand"],
                "receptor_id": record["mutation"],
                "mutation": record["mutation"],
                "rigid_delta": record["rigid_delta"],
                "flex_delta": record["flex_delta"],
                "pose_changed": record["pose_changed"],
                "known_resistance": record["known_resistance"],
            }
        )
    write_csv(CASE / "panel.csv", columns, rows)


def build_reference_poses() -> None:
    columns = [
        "candidate_id",
        "receptor_id",
        "pose_index",
        "rmsd_to_reference_A",
        "reference_conformer",
        "affinity_kcal_mol",
    ]
    rows = []
    for record in read_csv(ROOT / "struct_case" / "results" / "redock_summary.csv"):
        rmsd_a = float(record["rmsd_to_A"])
        rmsd_b = float(record["rmsd_to_B"])
        rows.append(
            {
                "candidate_id": "saquinavir",
                "receptor_id": "WT",
                "pose_index": record["pose"],
                "rmsd_to_reference_A": record["best_rmsd"],
                "reference_conformer": "A" if rmsd_a <= rmsd_b else "B",
                "affinity_kcal_mol": record["affinity_kcal_mol"],
            }
        )
    write_csv(CASE / "reference_poses.csv", columns, rows)


def main() -> None:
    smiles = smiles_from_a1()
    build_candidates(smiles)
    build_receptors()
    build_poses()
    build_panel()
    build_reference_poses()
    print(f"wrote stage-3 inputs to {CASE}")


if __name__ == "__main__":
    main()
