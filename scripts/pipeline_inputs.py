"""Stage02 -> Stage03 bridge: turn the frozen 02 JAK2 job into stage3 tidy inputs.

Every path written into the stage3 tables points at the 02 delivery; nothing is
copied out of it and nothing inside it is modified.  All hashes are recomputed
from the files on disk and cross-checked against the 02 job.json and the
delivery_index.json catalog, so a mismatch fails loudly instead of silently
producing a stage3 run on unknown inputs.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any

# Every path below is derived, never hard-coded for one machine:
#   A4S_ROOT   package root (defaults to the parent of the directory holding this file)
#   A4S_STAGE02_DELIVERY  path to a frozen 02 delivery (not shipped with this package)
#   A4S_RUNS   where stage-3 runs and configs are written
ROOT = Path(os.environ.get("A4S_ROOT") or Path(__file__).resolve().parent.parent)
DELIVERY = Path(
    os.environ.get("A4S_STAGE02_DELIVERY")
    or ROOT / "src" / "handoff_skill" / "a4s-stage02-delivery-v1.1.0"
)
RUNS = Path(os.environ.get("A4S_RUNS") or ROOT / "runs")
CASE = RUNS / "_stage3_case"

RUN02 = DELIVERY / "modules" / "JAK2" / "module02-module001" / "runs" / "jak2_acceptance_001"
INDEX = DELIVERY / "delivery_index.json"

ARTIFACTS = {
    "job": "job.json",
    "receptor": "outputs/receptor.pdbqt",
    "receptor_pdb": "outputs/receptor_prepared.pdb",
    "ligand": "outputs/ligand.pdbqt",
    "reference_sdf": "outputs/reference.sdf",
    "vina_box": "outputs/vina_box.txt",
}


class BridgeError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def heavy_atom_count(pdbqt: Path) -> int:
    n = 0
    for line in pdbqt.read_text(errors="replace").splitlines():
        if line.startswith(("ATOM", "HETATM")):
            tail = line[76:79].strip().upper() if len(line) >= 79 else ""
            elem = tail or line.split()[-1].upper()
            if not elem.startswith("H"):
                n += 1
    return n


def read_box(path: Path) -> dict[str, float]:
    box: dict[str, float] = {}
    for line in path.read_text().splitlines():
        key, value = line.split("=", 1)
        box[key.strip()] = float(value)
    expected = {"center_x", "center_y", "center_z", "size_x", "size_y", "size_z"}
    if set(box) != expected:
        raise BridgeError(f"unexpected vina box fields in {path}: {sorted(box)}")
    return box


def verify_against_delivery_index(hashes: dict[str, str]) -> dict[str, bool]:
    """Compare recomputed hashes with the frozen catalog in delivery_index.json."""
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    target = index["targets"]["JAK2"]
    declared = {
        "job": target["job"]["sha256"],
        "receptor": target["artifacts"]["receptor"]["sha256"],
        "ligand": target["artifacts"]["ligand"]["sha256"],
        "vina_box": target["artifacts"]["search_box"]["sha256"],
        "reference_sdf": target["artifacts"]["reference_ligand"]["sha256"],
    }
    result = {}
    for role, expected in declared.items():
        actual = hashes.get(role)
        result[role] = {
            "expected": expected,
            "actual": actual,
            "match": actual == expected,
        }
    return result


def collect_02_state() -> dict[str, Any]:
    job_path = RUN02 / ARTIFACTS["job"]
    if not job_path.is_file():
        raise BridgeError(f"missing 02 job.json: {job_path}")
    job = json.loads(job_path.read_text(encoding="utf-8"))

    hashes = {role: sha(RUN02 / rel) for role, rel in ARTIFACTS.items() if role != "receptor_pdb"}
    hashes["receptor_pdb"] = sha(RUN02 / ARTIFACTS["receptor_pdb"])
    crosscheck = verify_against_delivery_index(hashes)
    mismatched = [role for role, info in crosscheck.items() if not info["match"]]
    if mismatched:
        raise BridgeError(f"02 artifact hash mismatch vs delivery_index.json: {mismatched}")

    # the frozen job.json must agree with the files it points at
    for role in ("receptor", "ligand", "reference_sdf", "vina_box"):
        key = {"reference_sdf": "reference"}.get(role, role)
        declared = job["outputs"][key]["sha256"]
        if declared != hashes[role]:
            raise BridgeError(f"02 job.json declares a different {key} hash than the file on disk")

    box = read_box(RUN02 / ARTIFACTS["vina_box"])
    center = [round(v, 6) for v in job["search_box"]["center"]]
    size = [round(v, 6) for v in job["search_box"]["size"]]
    if center != [round(box["center_x"], 6), round(box["center_y"], 6), round(box["center_z"], 6)]:
        raise BridgeError("vina_box.txt center disagrees with job.json search_box.center")
    if size != [round(box["size_x"], 6), round(box["size_y"], 6), round(box["size_z"], 6)]:
        raise BridgeError("vina_box.txt size disagrees with job.json search_box.size")

    return {
        "job": job,
        "job_path": str(job_path),
        "job_sha256": hashes["job"],
        "preparation_status": job.get("preparation_status"),
        "ready_for_03": bool(job.get("ready_for_03")),
        "hashes": hashes,
        "delivery_index_crosscheck": crosscheck,
        "box": box,
        "receptor_pdbqt": str(RUN02 / ARTIFACTS["receptor"]),
        "receptor_pdb": str(RUN02 / ARTIFACTS["receptor_pdb"]),
        "ligand_pdbqt": str(RUN02 / ARTIFACTS["ligand"]),
        "reference_sdf": str(RUN02 / ARTIFACTS["reference_sdf"]),
        "heavy_atoms": heavy_atom_count(RUN02 / ARTIFACTS["ligand"]),
    }


def write_candidates(case: Path, state: dict[str, Any], role: str) -> Path:
    path = case / ("candidates.csv" if role == "control" else "candidates_candidate_role.csv")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "candidate_id", "name", "role", "smiles", "source", "chemotype", "heavy_atoms",
            "ligand_sdf_path", "ligand_pdbqt_path", "availability", "synthesizability",
            "solubility_risk", "selectivity_risk", "notes",
        ])
        writer.writerow([
            "RXT",
            "RXT (6VGL co-crystal ligand, redock control)",
            role,
            "",
            f"AI4ScienceHackthon 02 delivery, run {state['job']['job_id']}, outputs/ligand.pdbqt",
            "",
            str(state["heavy_atoms"]),
            state["reference_sdf"],
            state["ligand_pdbqt"],
            "", "", "", "",
            "Co-crystal ligand redock control, not a new inhibitor; docking score is not measured affinity.",
        ])
    return path


def write_receptors(case: Path, state: dict[str, Any]) -> Path:
    path = case / "receptors.csv"
    box = state["box"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "receptor_id", "label", "mutations", "pdb_path", "pdbqt_path", "flex_pdbqt_path",
            "box_center_x", "box_center_y", "box_center_z", "box_size_x", "box_size_y",
            "box_size_z", "is_primary",
        ])
        writer.writerow([
            "JAK2_6VGL",
            "6VGL auth A 840-1132, 02-prepared rigid receptor",
            "",
            state["receptor_pdb"],
            state["receptor_pdbqt"],
            "",
            f"{box['center_x']:.4f}", f"{box['center_y']:.4f}", f"{box['center_z']:.4f}",
            f"{box['size_x']:.4f}", f"{box['size_y']:.4f}", f"{box['size_z']:.4f}",
            "true",
        ])
    return path


def write_reference_poses(case: Path, redock: dict[str, Any]) -> Path:
    path = case / "reference_poses.csv"
    affinities = {int(k): v for k, v in redock["affinity_by_pose"].items()}
    with path.open("w", encoding="utf-8", newline="") as fh:
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
                "RXT", "JAK2_6VGL", index, f"{row['rmsd_A']:.3f}",
                "02 outputs/reference.sdf (RXT, 6VGL auth A 1201)", affinities.get(index, ""),
            ])
    return path


def write_config(
    case: Path,
    candidates: Path,
    receptors: Path,
    case_id: str,
    reference_poses: Path | None = None,
) -> Path:
    inputs: dict[str, str] = {
        "candidates": str(candidates),
        "receptors": str(receptors),
    }
    reference: dict[str, Any] = {"checks": []}
    if reference_poses is not None:
        inputs["reference_poses"] = str(reference_poses)
        reference = {
            "checks": [
                {
                    "id": "redock_rxt",
                    "type": "redock_rmsd",
                    "candidate_id": "RXT",
                    "receptor_id": "JAK2_6VGL",
                    "threshold": 2.0,
                    "required": True,
                    "impact": "blocks_advance",
                    "note": "Co-crystal RXT redock pose recovery (RDKit GetBestRMS vs 02 reference.sdf)",
                }
            ]
        }
    config = {
        "schema_version": "stage3-config/1",
        "case_id": case_id,
        "target": {"name": "JAK2 kinase domain (6VGL auth A 840-1132)", "uniprot": "O60674"},
        "inputs": inputs,
        "engine": {
            "name": "vina",
            "options": {
                "exhaustiveness": 8,
                "n_poses": 9,
                "seed": 42,
                "cpu": 4,
                "protocol_suffix": "rigid",
                "reference_ligand": str(RUN02 / ARTIFACTS["reference_sdf"]),
                "catalytic_selection": [
                    {"resname": "ASP", "resseq": 994, "atoms": ["OD1", "OD2"]}
                ],
            },
        },
        "reference": reference,
        "criteria": {
            "primary_receptor": "JAK2_6VGL",
            "min_site_overlap": 0.70,
            "min_catalytic_contacts": 0,
            "max_clashes": 0,
            "max_affinity_kcal_mol": -7.0,
            "min_ligand_efficiency": 0.18,
            "max_flex_delta": 1.0,
            "max_consensus_std": 0.08,
            "require_reference_pass": False,
            "allow_af3_only_advance": False,
        },
        "runs_dir": str(RUNS),
    }
    path = case / f"stage3_config_{case_id}.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
