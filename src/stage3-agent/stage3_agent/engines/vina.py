"""AutoDock Vina adapter.

Docking itself is delegated to the `vina` python package (imported lazily).
Geometry is computed from the written PDBQT poses plus the receptor PDB, so
RDKit is not required for QC.

A single failing receptor/ligand pair does not abort the run: the failure is
recorded as a note and an uncertainty, and the remaining pairs continue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import structure
from ..config import Config
from ..errors import EngineUnavailable
from ..intake import IntakeResult
from .base import EngineResult, ProgressCb


class VinaEngine:
    name = "vina"

    def generate(
        self,
        config: Config,
        intake: IntakeResult,
        run_dir: Path,
        progress: ProgressCb | None = None,
    ) -> EngineResult:
        try:
            from vina import Vina  # noqa: F401
        except ImportError as exc:
            raise EngineUnavailable(
                "vina",
                "pip install vina  (Windows 建议用 conda: conda install -c conda-forge vina)",
            ) from exc

        options = config.engine_options
        reference_ligand = options.get("reference_ligand")
        reference_atoms: list[structure.Atom] = []
        if reference_ligand:
            reference_path = config.resolve(reference_ligand)
            reference_atoms = _read_any_atoms(reference_path)

        catalytic_selection = options.get("catalytic_selection", [])
        receptor_cache: dict[str, list[structure.Atom]] = {}

        pairs = [
            (candidate, receptor)
            for candidate in intake.candidates
            if candidate["role"] in ("candidate", "control")
            for receptor in intake.receptors
        ]
        total = max(1, len(pairs))
        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        uncertainties: list[dict[str, Any]] = []
        pose_root = run_dir / "poses"

        for index, (candidate, receptor) in enumerate(pairs, start=1):
            label = f"{candidate['candidate_id']}@{receptor['receptor_id']}"
            if progress:
                progress(index, total, label)

            receptor_atoms = _receptor_atoms(config, receptor, receptor_cache)
            try:
                rows.extend(
                    _dock_pair(
                        config,
                        candidate,
                        receptor,
                        receptor_atoms,
                        reference_atoms,
                        catalytic_selection,
                        pose_root,
                        options,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - isolate one pair
                message = f"{label}: docking failed ({type(exc).__name__}: {exc})"
                notes.append(message)
                uncertainties.append(
                    {
                        "uncertainty_id": f"U-ENGINE-{candidate['candidate_id']}-{receptor['receptor_id']}",
                        "scope": label,
                        "kind": "docking_failed",
                        "statement": message,
                        "impact": "limits_ranking",
                        "resolves_with": "检查受体/配体准备、对接盒与 Vina 日志后重跑该对",
                        "status": "open",
                    }
                )

        if not rows:
            raise EngineUnavailable("vina", "所有受体-配体对均失败，见 events.jsonl")
        notes.append(
            f"Vina exhaustiveness={options.get('exhaustiveness', 16)}, "
            f"n_poses={options.get('n_poses', 9)}, seed={options.get('seed', 42)}"
        )
        return EngineResult(
            rows=rows, engine=self.name, notes=notes, uncertainties=uncertainties
        )


def _dock_pair(
    config: Config,
    candidate: dict[str, str],
    receptor: dict[str, str],
    receptor_atoms: list[structure.Atom],
    reference_atoms: list[structure.Atom],
    catalytic_selection: list[dict],
    pose_root: Path,
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    from vina import Vina

    candidate_id = candidate["candidate_id"]
    receptor_id = receptor["receptor_id"]
    ligand_path = config.resolve(candidate["ligand_pdbqt_path"])
    receptor_path = config.resolve(receptor["pdbqt_path"])
    if not ligand_path.exists():
        raise FileNotFoundError(f"ligand pdbqt not found: {ligand_path}")
    if not receptor_path.exists():
        raise FileNotFoundError(f"receptor pdbqt not found: {receptor_path}")

    out_dir = pose_root / candidate_id / receptor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    poses_path = out_dir / "poses.pdbqt"

    vina = Vina(sf_name="vina", cpu=int(options.get("cpu", 4)), seed=int(options.get("seed", 42)), verbosity=0)
    vina.set_receptor(str(receptor_path))
    vina.set_ligand_from_file(str(ligand_path))
    center = [
        float(receptor["box_center_x"]),
        float(receptor["box_center_y"]),
        float(receptor["box_center_z"]),
    ]
    box = [
        float(receptor["box_size_x"]),
        float(receptor["box_size_y"]),
        float(receptor["box_size_z"]),
    ]
    vina.compute_vina_maps(center=center, box_size=box)
    n_poses = int(options.get("n_poses", 9))
    vina.dock(exhaustiveness=int(options.get("exhaustiveness", 16)), n_poses=n_poses)
    vina.write_poses(str(poses_path), n_poses=n_poses, overwrite=True)
    energies = vina.energies(n_poses=n_poses)

    models = structure.split_pdbqt_models(poses_path)
    if not models:
        raise RuntimeError("vina produced no poses")
    rows = []
    for pose_index, atoms in enumerate(models, start=1):
        affinity = float(energies[pose_index - 1][0]) if pose_index - 1 < len(energies) else None
        heavy = structure.heavy(atoms)
        heavy_count = len(heavy)
        overlaps = (
            structure.site_overlap(atoms, reference_atoms) if reference_atoms else None
        )
        clashes = (
            structure.clash_pairs(atoms, receptor_atoms) if receptor_atoms else None
        )
        polar = (
            structure.count_contacts(
                heavy, receptor_atoms, 3.5, left_polar_only=True, right_polar_only=True
            )
            if receptor_atoms
            else None
        )
        if catalytic_selection and receptor_atoms:
            catalytic, nearest = structure.catalytic_contacts(
                atoms, receptor_atoms, catalytic_selection, cutoff=3.5
            )
        else:
            catalytic, nearest = None, None
        centroid_distance = (
            _centroid_distance(atoms, reference_atoms) if reference_atoms else None
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "receptor_id": receptor_id,
                "pose_index": pose_index,
                "protocol": f"vina_{options.get('protocol_suffix', 'rigid')}",
                "protocol_role": "primary",
                "engine": "vina",
                "affinity_kcal_mol": affinity,
                "heavy_atoms": heavy_count,
                "ligand_efficiency": round(abs(affinity) / heavy_count, 3)
                if affinity is not None and heavy_count
                else None,
                "centroid_distance_to_crystal_A": centroid_distance,
                "site_overlap_fraction": overlaps,
                "min_distance_to_Asp25_oxygens": nearest,
                "asp25_contacts_under_3.5A": catalytic,
                "clash_pairs": clashes,
                "polar_contacts": polar,
                "geometry_source": "sdf" if receptor_atoms else "table",
                "confidence_score": None,
                "confidence_type": None,
            }
        )
    return rows


def _receptor_atoms(
    config: Config,
    receptor: dict[str, str],
    cache: dict[str, list[structure.Atom]],
) -> list[structure.Atom]:
    pdb_path = receptor.get("pdb_path")
    if not pdb_path:
        return []
    resolved = str(config.resolve(pdb_path))
    if resolved not in cache:
        cache[resolved] = structure.read_pdb_atoms(resolved)
    return cache[resolved]


def _read_any_atoms(path: Path) -> list[structure.Atom]:
    if not path.exists():
        return []
    suffix = path.suffix.lower()
    if suffix == ".sdf":
        return structure.read_sdf_atoms(path)
    if suffix == ".pdbqt":
        return structure.read_pdbqt_atoms(path)
    return structure.read_pdb_atoms(path)


def _centroid_distance(
    ligand: list[structure.Atom], reference: list[structure.Atom]
) -> float:
    a = structure.centroid(structure.heavy(ligand))
    b = structure.centroid(structure.heavy(reference))
    return round(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5, 2)
