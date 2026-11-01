"""Build stage-3 tidy inputs for one case from a finished prep/dock run.

Reads (all produced by the case scripts in this directory):
    <run>/inputs/prep_inputs.json      box center/size, protein file
    <run>/ligand/ligand_prep.json      co-crystal pose sdf, ligand pdbqt, smiles
    <run>/receptor/receptor_prep.json  receptor pdbqt
    <run>/docking/redock_rmsd.json     per-pose RMSD + affinities

Writes (into --out, default <run>/stage3_case):
    candidates.csv, receptors.csv, reference_poses.csv,
    stage3_config_<case_id>.json

All paths inside the generated CSVs/config are absolute paths inside <run>, so the
stage-3 agent reads the same files this run just produced -- nothing is hand-typed
and nothing is copied.

Run with any Python 3.11 (standard library only).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run directory holding prep/dock outputs")
    parser.add_argument("--case", required=True, help="path to the case.json of this reproduction case")
    parser.add_argument("--out", default=None, help="stage-3 case directory (default <run>/stage3_case)")
    parser.add_argument("--redock", default=None,
                        help="redock_rmsd.json written by scripts/a4s_redock.py "
                             "(default <run>/docking/redock_rmsd.json)")
    args = parser.parse_args()

    run = Path(args.run).resolve()
    case_spec = load(Path(args.case).resolve())
    out = Path(args.out).resolve() if args.out else run / "stage3_case"
    out.mkdir(parents=True, exist_ok=True)

    prep = load(run / "inputs" / "prep_inputs.json")
    lig = load(run / "ligand" / "ligand_prep.json")
    rec = load(run / "receptor" / "receptor_prep.json")
    redock_path = Path(args.redock).resolve() if args.redock else run / "docking" / "redock_rmsd.json"
    redock = load(redock_path)

    stage3 = case_spec["stage3"]
    receptor_id = stage3["receptor_id"]
    candidate_id = stage3["candidate_id"]
    case_id = stage3["case_id"]
    center = prep["box_center"]
    size = prep["box_size"]

    # ---------------- candidates.csv ----------------
    cand = out / "candidates.csv"
    with cand.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "candidate_id", "name", "role", "smiles", "source", "chemotype", "heavy_atoms",
            "ligand_sdf_path", "ligand_pdbqt_path", "availability", "synthesizability",
            "solubility_risk", "selectivity_risk", "notes",
        ])
        writer.writerow([
            candidate_id,
            stage3["candidate_name"],
            "candidate",
            lig["pose_smiles"],
            f"{case_spec['target']['pdb_id']} HETATM {case_spec['ligand']['resname']} (co-crystal) "
            f"+ PubChem CID {case_spec['ligand']['pubchem_cid']} (bond template)",
            "hydroxyethylamine sulfonamide protease inhibitor",
            str(lig["nr_heavy_atoms"]),
            lig["cocrystal_pose_sdf"],
            lig["ligand_pdbqt"],
            "", "", "", "",
            "Co-crystal ligand redock control; docking score is not measured affinity.",
        ])

    # ---------------- receptors.csv ----------------
    recp = out / "receptors.csv"
    with recp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "receptor_id", "label", "mutations", "pdb_path", "pdbqt_path", "flex_pdbqt_path",
            "box_center_x", "box_center_y", "box_center_z", "box_size_x", "box_size_y",
            "box_size_z", "is_primary",
        ])
        writer.writerow([
            receptor_id,
            stage3["receptor_label"],
            "",
            rec["input_pdb"],
            rec["receptor_pdbqt"],
            "",
            f"{center[0]:.4f}", f"{center[1]:.4f}", f"{center[2]:.4f}",
            f"{size[0]:.4f}", f"{size[1]:.4f}", f"{size[2]:.4f}",
            "true",
        ])

    # ---------------- reference_poses.csv ----------------
    ref = out / "reference_poses.csv"
    affinities = {int(k): v for k, v in redock["affinity_by_pose"].items()}
    with ref.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "candidate_id", "receptor_id", "pose_index", "rmsd_to_reference_A",
            "reference_conformer", "affinity_kcal_mol",
        ])
        for row in redock["poses"]:
            if row["rmsd_A"] is None:
                continue
            index = int(row["pose_index"])
            writer.writerow([
                candidate_id, receptor_id, index, f"{row['rmsd_A']:.3f}",
                f"{case_spec['target']['pdb_id']} HETATM {case_spec['ligand']['resname']} "
                f"co-crystal pose (ligand/cocrystal_pose.sdf)",
                affinities.get(index, ""),
            ])

    # ---------------- stage3 config ----------------
    check = dict(stage3["reference_check"])
    check.update({"candidate_id": candidate_id, "receptor_id": receptor_id})
    config = {
        "schema_version": "stage3-config/1",
        "case_id": case_id,
        "target": {"name": case_spec["target"]["name"], "uniprot": case_spec["target"]["uniprot"]},
        "inputs": {"candidates": str(cand), "receptors": str(recp), "reference_poses": str(ref)},
        "engine": {
            "name": "vina",
            "options": {
                "exhaustiveness": case_spec["engine"]["exhaustiveness"],
                "n_poses": case_spec["engine"]["n_poses"],
                "seed": case_spec["engine"]["seed"],
                "cpu": case_spec["engine"]["cpu"],
                "protocol_suffix": "rigid",
                "reference_ligand": lig["cocrystal_pose_sdf"],
                "catalytic_selection": stage3["catalytic_selection"],
            },
        },
        "reference": {"checks": [check]},
        "criteria": stage3["criteria"],
        "runs_dir": str(run / "stage3_runs"),
    }
    cfg_path = out / f"stage3_config_{case_id}.json"
    cfg_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    payload = {
        "candidates": str(cand),
        "receptors": str(recp),
        "reference_poses": str(ref),
        "config": str(cfg_path),
        "box_center": center,
        "box_size": size,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
