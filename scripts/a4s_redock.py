"""Compute a real redock RMSD between Vina poses and the 02 experimental conformer.

pose pdbqt --meeko mk_export--> SDF --RDKit GetBestRMS--> RMSD vs 02 outputs/reference.sdf

Run with a Python that has rdkit + meeko (requirements/science.txt).
Writes JSON containing per-pose RMSD plus the affinities read from the pose pdbqt,
so the caller can build stage3's reference_poses table without hand-typed numbers.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

RESULT_RE = re.compile(r"^REMARK VINA RESULT:\s+(-?\d+\.\d+)")


def split_models(text: str) -> list[str]:
    models: list[str] = []
    current: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("MODEL"):
            inside = True
            current = [line]
            continue
        if line.startswith("ENDMDL"):
            current.append(line)
            models.append("\n".join(current) + "\n")
            inside = False
            continue
        if inside:
            current.append(line)
    return models


def affinities(text: str) -> list[float]:
    return [float(m.group(1)) for m in (RESULT_RE.match(l.strip()) for l in text.splitlines()) if m]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poses", required=True)
    parser.add_argument("--reference-sdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args()

    from rdkit import Chem
    from rdkit.Chem import rdMolAlign, rdMolDescriptors

    poses_path = Path(args.poses)
    out_path = Path(args.out)
    workdir = Path(args.workdir) if args.workdir else out_path.parent / "redock_poses"
    workdir.mkdir(parents=True, exist_ok=True)

    text = poses_path.read_text(errors="replace")
    models = split_models(text)
    aff = affinities(text)

    reference = Chem.MolFromMolFile(args.reference_sdf, removeHs=True, sanitize=True)
    if reference is None:
        print(f"error: cannot read reference sdf {args.reference_sdf}", file=sys.stderr)
        return 1

    rows = []
    for index, model in enumerate(models, start=1):
        pdbqt = workdir / f"pose{index}.pdbqt"
        sdf = workdir / f"pose{index}.sdf"
        pdbqt.write_text(model)
        proc = subprocess.run(
            [sys.executable, "-m", "meeko.cli.mk_export", str(pdbqt), "-s", str(sdf)],
            capture_output=True, text=True,
        )
        rmsd = None
        if proc.returncode == 0 and sdf.is_file():
            mol = Chem.MolFromMolFile(str(sdf), removeHs=True, sanitize=True)
            if mol is not None and mol.GetNumAtoms() == reference.GetNumAtoms():
                rmsd = round(float(rdMolAlign.GetBestRMS(mol, reference)), 3)
        rows.append({"pose_index": index, "rmsd_A": rmsd})

    valid = [r for r in rows if r["rmsd_A"] is not None]
    payload = {
        "method": "meeko mk_export (pdbqt->sdf) + RDKit rdMolAlign.GetBestRMS (symmetry aware)",
        "poses_pdbqt": str(poses_path),
        "reference_sdf": args.reference_sdf,
        "reference_heavy_atoms": reference.GetNumAtoms(),
        "reference_formula": rdMolDescriptors.CalcMolFormula(reference),
        "poses": rows,
        "affinity_by_pose": {str(i): a for i, a in enumerate(aff, start=1)},
        "best_pose": min(valid, key=lambda r: r["rmsd_A"]) if valid else None,
        "note": "RMSD is pose-recovery evidence for the co-crystal control only; not affinity or activity.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"best_pose": payload["best_pose"], "reference_heavy_atoms": reference.GetNumAtoms()},
                     ensure_ascii=False))
    return 0 if valid else 2


if __name__ == "__main__":
    sys.exit(main())
