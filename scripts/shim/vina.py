"""Minimal `vina` Python-API shim backed by the real AutoDock Vina 1.2.5 CLI.

WHY THIS EXISTS
---------------
No `vina` wheel is published for Windows on PyPI (checked vina 1.2.0.dev1 ..
1.2.7: win wheels = none, only sdist which needs Boost + SWIG to compile).
Stage 3's engine adapter imports `from vina import Vina` and raises
`engine not available` otherwise. This module maps exactly that subset of the
official python API onto calls to the official Windows binary
(AutoDock_Vina v1.2.5, sha256 b5a23b226ee5dc1b028df04082d13a7b0e5b9b12b988bbcf9498cb351e20b38f).

WHAT IT DOES NOT DO
-------------------
It does not score, guess or interpolate anything. Every affinity returned by
`energies()` comes from the `REMARK VINA RESULT` records that vina.exe wrote
into the output PDBQT. The exact command line of every run is kept in
`Vina.last_command` for the audit trail.

API surface implemented (as used by stage3_agent/engines/vina.py):
    Vina(sf_name=, cpu=, seed=, verbosity=)
    .set_receptor(path)
    .set_ligand_from_file(path)
    .compute_vina_maps(center=[x,y,z], box_size=[x,y,z])
    .dock(exhaustiveness=int, n_poses=int)
    .write_poses(path, n_poses=int, overwrite=bool)
    .energies(n_poses=int) -> list[[affinity, rmsd_lb, rmsd_ub], ...]
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

__shim__ = True
__version__ = "1.2.5-shim"

VINA_EXE = Path(
    os.environ.get(
        "A4S_VINA_EXE", Path(__file__).resolve().parents[2] / "tools" / "vina_1.2.5_win.exe"
    )
)


def version() -> str:
    """Return the banner of the underlying Vina binary."""
    if not VINA_EXE.is_file():
        raise RuntimeError(f"vina executable not found: {VINA_EXE}")
    out = subprocess.run(
        [str(VINA_EXE), "--version"], capture_output=True, text=True, timeout=120
    )
    return (out.stdout or out.stderr).strip()


class Vina:
    def __init__(self, sf_name: str = "vina", cpu: int = 4, seed: int = 42, verbosity: int = 0):
        if sf_name not in {"vina", "vinardo", "ad4"}:
            raise ValueError(f"unsupported scoring function: {sf_name}")
        self.sf_name = sf_name
        self.cpu = int(cpu)
        self.seed = int(seed)
        self.verbosity = int(verbosity)
        self.receptor_path: str | None = None
        self.ligand_path: str | None = None
        self.center: list[float] | None = None
        self.box_size: list[float] | None = None
        self.last_command: list[str] | None = None
        self.last_log: str = ""
        self._poses_path: Path | None = None
        self._energies: list[list[float]] = []

    # ---- inputs -----------------------------------------------------------
    def set_receptor(self, path: str, maps: object | None = None) -> None:
        if not Path(path).is_file():
            raise FileNotFoundError(f"receptor pdbqt not found: {path}")
        self.receptor_path = str(path)

    def set_ligand_from_file(self, path: str, add_hydrogens: bool = True) -> None:
        if not Path(path).is_file():
            raise FileNotFoundError(f"ligand pdbqt not found: {path}")
        self.ligand_path = str(path)

    def compute_vina_maps(self, center=None, box_size=None, **_: object) -> None:
        if center is None or box_size is None:
            raise ValueError("center and box_size are required")
        self.center = [float(v) for v in center]
        self.box_size = [float(v) for v in box_size]
        if len(self.center) != 3 or len(self.box_size) != 3:
            raise ValueError("center and box_size need three values each")

    # ---- docking ----------------------------------------------------------
    def dock(
        self,
        exhaustiveness: int = 8,
        n_poses: int = 9,
        min_rmsd: float = 1.0,
        max_evals: int = 0,
        timeout: int = 0,
    ) -> None:
        if not (self.receptor_path and self.ligand_path):
            raise RuntimeError("receptor and ligand must be set before dock()")
        if self.center is None or self.box_size is None:
            raise RuntimeError("compute_vina_maps() must be called before dock()")
        if not VINA_EXE.is_file():
            raise RuntimeError(f"vina executable not found: {VINA_EXE}")

        tmpdir = Path(tempfile.mkdtemp(prefix="vina_shim_"))
        out_pdbqt = tmpdir / "poses.pdbqt"
        cmd = [
            str(VINA_EXE),
            "--receptor", self.receptor_path,
            "--ligand", self.ligand_path,
            "--scoring", self.sf_name,
            "--center_x", f"{self.center[0]:.4f}",
            "--center_y", f"{self.center[1]:.4f}",
            "--center_z", f"{self.center[2]:.4f}",
            "--size_x", f"{self.box_size[0]:.4f}",
            "--size_y", f"{self.box_size[1]:.4f}",
            "--size_z", f"{self.box_size[2]:.4f}",
            "--exhaustiveness", str(int(exhaustiveness)),
            "--num_modes", str(int(n_poses)),
            "--seed", str(self.seed),
            "--cpu", str(self.cpu),
            "--out", str(out_pdbqt),
        ]
        self.last_command = cmd
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or None)
        # vina 1.2.5 on Windows rejects --log in this build, so stdout carries the log.
        self.last_log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError(
                f"vina.exe exited {proc.returncode}: {(proc.stderr or proc.stdout or '')[-2000:]}"
            )
        if not out_pdbqt.is_file() or out_pdbqt.stat().st_size == 0:
            raise RuntimeError("vina.exe produced no output poses")
        self._poses_path = out_pdbqt
        self._energies = _parse_vina_results(out_pdbqt)
        if not self._energies:
            raise RuntimeError("no REMARK VINA RESULT records in vina output")

    def write_poses(self, path: str, n_poses: int = 9, overwrite: bool = True) -> None:
        if self._poses_path is None:
            raise RuntimeError("dock() must run before write_poses()")
        dest = Path(path)
        if dest.exists() and not overwrite:
            raise FileExistsError(f"poses file exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._poses_path, dest)
        _truncate_models(dest, int(n_poses))

    def energies(self, n_poses: int = 9) -> list[list[float]]:
        return [row[:] for row in self._energies[: int(n_poses)]]


_RESULT_RE = re.compile(r"^REMARK VINA RESULT:\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)")


def _parse_vina_results(path: Path) -> list[list[float]]:
    rows: list[list[float]] = []
    for line in path.read_text(errors="replace").splitlines():
        match = _RESULT_RE.match(line.strip())
        if match:
            rows.append([float(match.group(1)), float(match.group(2)), float(match.group(3))])
    return rows


def _truncate_models(path: Path, n_poses: int) -> None:
    """Keep only the first `n_poses` MODEL blocks, mirroring the real API."""
    if n_poses <= 0:
        return
    lines = path.read_text(errors="replace").splitlines(keepends=True)
    out: list[str] = []
    seen = 0
    for line in lines:
        if line.startswith("MODEL"):
            seen += 1
            if seen > n_poses:
                break
        out.append(line)
    if seen > n_poses:
        out.append("END\n")
    path.write_text("".join(out))
