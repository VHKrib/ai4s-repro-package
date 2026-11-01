"""Single-command smoke test for a4s-repro.

    python tests/smoke_test.py

What it does (no docking, no network, ~2 s):
    1. package layout        every file the README promises exists
    2. case inputs           sha256 of the crystal structure / ligand template match case.json
    3. Vina binary           sha256 matches case.json and the binary really executes
    4. reference run         the bundled stage-3 reference run is `completed`
    5. validate              stage-3 re-reads every output and re-checks its sha256
    6. numbers              9 poses, best -9.958 kcal/mol, redock RMSD 0.211 A

Exit code 0 = everything passed. This test never runs AutoDock Vina docking; use
scripts/run_pipeline.py for a full re-computation.

Requires an interpreter with the standard library only (stage-3 has no third-party
dependencies). If envs/py3119-stage3 exists it is used for the stage-3 steps.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
CASE = PKG / "cases" / "hiv1_1t3r_darunavir"

LAYOUT = [
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES",
    "requirements/science.txt",
    "requirements/stage3.txt",
    "setup_envs.ps1",
    "tools/vina_1.2.5_win.exe",
    "scripts/run_pipeline.py",
    "scripts/a4s_redock.py",
    "scripts/pipeline_inputs.py",
    "scripts/sciencepal_bridge.py",
    "scripts/af3_to_stage3_manifest.py",
    "scripts/shim/vina.py",
    "src/stage3-agent/stage3.py",
    "src/module02-portability-win/scripts/run_prepare.py",
    "cases/hiv1_1t3r_darunavir/case.json",
    "cases/hiv1_1t3r_darunavir/scripts/prep_inputs.py",
    "cases/hiv1_1t3r_darunavir/scripts/prep_receptor.py",
    "cases/hiv1_1t3r_darunavir/scripts/prep_ligand.py",
    "cases/hiv1_1t3r_darunavir/scripts/dock_vina.py",
    "cases/hiv1_1t3r_darunavir/scripts/build_stage3_case.py",
    "docs/方法说明.md",
    "docs/证据索引.md",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, bool, str]] = []

    def add(self, name: str, expected, actual, ok: bool, note: str = "") -> None:
        self.rows.append((name, str(expected), bool(ok), f"{actual}" + (f"  ({note})" if note else "")))
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<28} expected={expected!s:<28} actual={actual}"
              + (f"  ({note})" if note else ""))

    def failed(self) -> list[str]:
        return [r[0] for r in self.rows if not r[2]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", default=str(CASE))
    parser.add_argument("--stage3-python", default=None,
                        help="interpreter for the stage-3 validate step "
                             "(default: envs/py3119-stage3, else this interpreter)")
    args = parser.parse_args()

    case_dir = Path(args.case).resolve()
    spec = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    expected = spec["expected"]
    report = Report()

    print(f"package root : {PKG}")
    print(f"case         : {case_dir}")
    print("-" * 100)

    # 1. layout
    missing = [rel for rel in LAYOUT if not (PKG / rel).is_file()]
    report.add("layout.files_present", f"{len(LAYOUT)} files", f"{len(LAYOUT) - len(missing)} found",
               not missing, f"missing: {missing}" if missing else "")

    # 2. case inputs
    for key, info in spec["inputs"].items():
        path = case_dir / info["path"]
        actual = sha256(path) if path.is_file() else "<missing>"
        report.add(f"input.{key}", info["sha256"][:16] + "…", actual[:16] + "…", actual == info["sha256"])

    # 3. vina binary
    vina = PKG / "tools" / "vina_1.2.5_win.exe"
    vina_sha = sha256(vina) if vina.is_file() else "<missing>"
    report.add("vina.sha256", spec["engine"]["binary_sha256"][:16] + "…", vina_sha[:16] + "…",
               vina_sha == spec["engine"]["binary_sha256"])
    banner = ""
    if vina.is_file():
        proc = subprocess.run([str(vina), "--version"], capture_output=True, text=True, timeout=120)
        banner = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[0] if (
            proc.stdout or proc.stderr) else ""
    report.add("vina.executes", "AutoDock Vina v1.2.5", banner, "v1.2.5" in banner)

    # 4. reference run
    ref_run = case_dir / "evidence" / "reference_run"
    state = json.loads((ref_run / "run_state.json").read_text(encoding="utf-8")) \
        if (ref_run / "run_state.json").is_file() else {}
    report.add("reference_run.status", "completed", state.get("status", "<missing>"),
               state.get("status") == "completed")

    # 5. validate (re-reads every output, re-checks every sha256)
    stage3_py = Path(args.stage3_python) if args.stage3_python else None
    if stage3_py is None:
        candidate = PKG / "envs" / "py3119-stage3" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        stage3_py = candidate if candidate.is_file() else Path(sys.executable)
    proc = subprocess.run(
        [str(stage3_py), "-B", "stage3.py", "validate", "--runs-dir", str(ref_run.parent),
         "--run-id", ref_run.name],
        cwd=str(PKG / "src" / "stage3-agent"), capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    line = (proc.stdout or proc.stderr).strip().splitlines()[-1] if (proc.stdout or proc.stderr).strip() else ""
    report.add("stage3.validate", f"OK  {expected['stage3_validate_outputs']} outputs validated",
               f"exit {proc.returncode}: {line}", proc.returncode == 0 and line.startswith("OK"),
               f"interpreter={stage3_py.name}")
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)

    # 6. numbers
    metrics = read_csv_rows(ref_run / "outputs" / "pose_metrics.csv")
    affinities = [round(float(r["affinity_kcal_mol"]), 3) for r in metrics]
    report.add("reference_run.poses", expected["n_poses"], len(metrics), len(metrics) == expected["n_poses"])
    report.add("reference_run.affinities", expected["affinities_kcal_mol"], affinities,
               affinities == expected["affinities_kcal_mol"])
    refcheck = read_csv_rows(ref_run / "outputs" / "reference_check.csv")
    rmsd = float(refcheck[0]["metric_value"]) if refcheck else None
    report.add("reference_run.redock_rmsd_A", expected["redock_rmsd_A"], rmsd,
               rmsd is not None and abs(rmsd - expected["redock_rmsd_A"]) <= 0.02,
               f"status={refcheck[0]['status'] if refcheck else 'missing'}")

    print("-" * 100)
    failed = report.failed()
    print(f"{len(report.rows) - len(failed)}/{len(report.rows)} checks passed"
          f"  ->  {'SMOKE TEST PASS' if not failed else 'SMOKE TEST FAIL: ' + ', '.join(failed)}")
    print("note: this test does not run docking; scripts/run_pipeline.py does the full re-computation.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
