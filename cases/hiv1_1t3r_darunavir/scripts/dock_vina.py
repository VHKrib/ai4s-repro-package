"""Stage D: real AutoDock Vina 1.2.5 docking of darunavir into 1T3R.

Runs through shim/vina.py (the same python-API shim the stage3 engine uses, backed by
the official Windows binary), so the exact command line and the complete Vina log are
captured verbatim. Writes poses, scores, per-pose pdbqt files, command and log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# shim/vina.py lives in <package>/scripts/shim; A4S_SHIM overrides for custom layouts.
SHIM = Path(os.environ.get("A4S_SHIM") or (Path(__file__).resolve().parents[3] / "scripts" / "shim"))
sys.path.insert(0, str(SHIM))

from vina import Vina, version as vina_version, VINA_EXE  # noqa: E402

rundir = Path(sys.argv[1])
receptor = Path(sys.argv[2])
ligand = Path(sys.argv[3])
center = [float(x) for x in sys.argv[4:7]]
box = [float(x) for x in sys.argv[7:10]]
exhaustiveness = int(sys.argv[10]) if len(sys.argv) > 10 else 8
n_poses = int(sys.argv[11]) if len(sys.argv) > 11 else 9
seed = int(sys.argv[12]) if len(sys.argv) > 12 else 42
cpu = int(sys.argv[13]) if len(sys.argv) > 13 else 4

docking = rundir / "docking"
docking.mkdir(parents=True, exist_ok=True)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


v = Vina(sf_name="vina", cpu=cpu, seed=seed, verbosity=0)
v.set_receptor(str(receptor))
v.set_ligand_from_file(str(ligand))
v.compute_vina_maps(center=center, box_size=box)

started = time.time()
v.dock(exhaustiveness=exhaustiveness, n_poses=n_poses)
elapsed = round(time.time() - started, 2)

poses_path = docking / "poses.pdbqt"
v.write_poses(str(poses_path), n_poses=n_poses, overwrite=True)
energies = v.energies(n_poses=n_poses)

(docking / "vina.log").write_text(v.last_log, encoding="utf-8")
cmdfile = docking / "vina_command.txt"
cmdfile.write_text(" ".join(v.last_command) + "\n", encoding="utf-8")

# split the 9 poses into individual files
text = poses_path.read_text()
blocks = re.findall(r"^MODEL.*?^ENDMDL$", text, flags=re.M | re.S)
pose_files = []
for i, block in enumerate(blocks, start=1):
    p = docking / "poses" / f"pose_{i}.pdbqt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(block.rstrip() + "\nEND\n", encoding="utf-8")
    pose_files.append(str(p))

payload = {
    "engine": "AutoDock Vina",
    "vina_banner": vina_version(),
    "vina_binary": str(VINA_EXE),
    "vina_binary_sha256": sha256(VINA_EXE),
    "python_binding": "shim/vina.py (python API -> official vina.exe CLI)",
    "receptor_pdbqt": str(receptor),
    "receptor_pdbqt_sha256": sha256(receptor),
    "ligand_pdbqt": str(ligand),
    "ligand_pdbqt_sha256": sha256(ligand),
    "parameters": {
        "scoring_function": "vina",
        "exhaustiveness": exhaustiveness,
        "n_poses": n_poses,
        "seed": seed,
        "cpu": cpu,
        "box_center": center,
        "box_size": box,
    },
    "command": v.last_command,
    "command_file": str(cmdfile),
    "elapsed_s": elapsed,
    "log_path": str(docking / "vina.log"),
    "log_bytes": (docking / "vina.log").stat().st_size,
    "poses_pdbqt": str(poses_path),
    "poses_pdbqt_sha256": sha256(poses_path),
    "n_pose_models": len(blocks),
    "pose_files": pose_files,
    "scores": [
        {"pose_index": i + 1, "affinity_kcal_mol": e[0], "rmsd_lb": e[1], "rmsd_ub": e[2]}
        for i, e in enumerate(energies)
    ],
}
(docking / "docking_result.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: payload[k] for k in ("vina_banner", "elapsed_s", "n_pose_models",
                                         "scores", "command")}, ensure_ascii=False, indent=2))
print("--- vina log (stdout) head/tail ---")
log = v.last_log.splitlines()
print("\n".join(log[:6]))
print("...")
print("\n".join(log[-25:]))
