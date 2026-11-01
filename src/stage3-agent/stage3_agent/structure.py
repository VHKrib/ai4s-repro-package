"""Minimal structure-file parsing and geometric metrics.

Deliberately dependency-free: PDB, PDBQT and V2000 SDF are parsed with fixed
columns, which is enough for clash / contact / overlap checks. RDKit is only
needed for chemistry-aware work (protonation, bond orders), not for these
distances.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Atom:
    x: float
    y: float
    z: float
    element: str
    resname: str = ""
    resseq: int = 0
    name: str = ""
    chain: str = ""

    @property
    def is_heavy(self) -> bool:
        element = self.element.upper().strip()
        return element not in ("H", "D", "") and not element.startswith("H")

    @property
    def is_polar(self) -> bool:
        element = self.element.upper().strip()
        return element[:1] in ("N", "O")


def read_pdb_atoms(path: str | Path, *, model: int = 1) -> list[Atom]:
    atoms: list[Atom] = []
    current = 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("MODEL"):
            current += 1
            continue
        if line.startswith("ENDMDL"):
            if current >= model:
                break
            continue
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if current and current != model:
            continue
        try:
            atoms.append(
                Atom(
                    x=float(line[30:38]),
                    y=float(line[38:46]),
                    z=float(line[46:54]),
                    element=_pdb_element(line),
                    resname=line[17:20].strip(),
                    resseq=int(float(line[22:26])),
                    name=line[12:16].strip(),
                    chain=line[21].strip(),
                )
            )
        except (ValueError, IndexError):
            continue
    return atoms


def _pdb_element(line: str) -> str:
    element = line[76:78].strip() if len(line) >= 78 else ""
    if element:
        return element.upper()
    name = line[12:16].strip()
    # Protein atom names start with the element symbol; CA is carbon alpha,
    # not calcium, in this context.
    return name[:1].upper() if name else "C"


def split_pdbqt_models(path: str | Path) -> list[list[Atom]]:
    models: list[list[Atom]] = []
    current: list[Atom] = []
    in_model = False
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("MODEL"):
            in_model = True
            current = []
            continue
        if line.startswith("ENDMDL"):
            if current:
                models.append(current)
            current = []
            in_model = False
            continue
        if line.startswith(("ATOM", "HETATM")):
            atom = _pdbqt_atom(line)
            if atom is not None:
                current.append(atom)
    if current:
        models.append(current)
    return models


def read_pdbqt_atoms(path: str | Path) -> list[Atom]:
    models = split_pdbqt_models(path)
    if models:
        return models[0]
    # Some writers emit a single pose without MODEL/ENDMDL.
    atoms: list[Atom] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(("ATOM", "HETATM")):
            atom = _pdbqt_atom(line)
            if atom is not None:
                atoms.append(atom)
    return atoms


def _pdbqt_atom(line: str) -> Atom | None:
    try:
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
    except (ValueError, IndexError):
        parts = line.split()
        if len(parts) < 9:
            return None
        try:
            x, y, z = float(parts[5]), float(parts[6]), float(parts[7])
        except ValueError:
            return None
    atom_type = line[77:79].strip() if len(line) >= 79 else ""
    if not atom_type:
        parts = line.split()
        atom_type = parts[-1] if parts else "C"
    return Atom(
        x=x,
        y=y,
        z=z,
        element=atom_type.upper(),
        resname=line[17:20].strip(),
        resseq=_safe_int(line[22:26]),
        name=line[12:16].strip(),
        chain=line[21].strip(),
    )


def read_sdf_atoms(path: str | Path) -> list[Atom]:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 4:
        return []
    try:
        atom_count = int(lines[3][0:3])
    except ValueError:
        return []
    atoms = []
    for line in lines[4 : 4 + atom_count]:
        try:
            atoms.append(
                Atom(
                    x=float(line[0:10]),
                    y=float(line[10:20]),
                    z=float(line[20:30]),
                    element=line[31:34].strip().upper() or "C",
                )
            )
        except (ValueError, IndexError):
            continue
    return atoms


def _safe_int(text: str) -> int:
    try:
        return int(float(text.strip()))
    except ValueError:
        return 0


def heavy(atoms: list[Atom]) -> list[Atom]:
    return [atom for atom in atoms if atom.is_heavy]


def centroid(atoms: list[Atom]) -> tuple[float, float, float]:
    if not atoms:
        return (0.0, 0.0, 0.0)
    n = len(atoms)
    return (
        sum(a.x for a in atoms) / n,
        sum(a.y for a in atoms) / n,
        sum(a.z for a in atoms) / n,
    )


def distance(a: Atom, b: Atom) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def min_distance(left: list[Atom], right: list[Atom]) -> float | None:
    best: float | None = None
    for a in left:
        for b in right:
            d = distance(a, b)
            if best is None or d < best:
                best = d
    return best


def count_contacts(
    left: list[Atom],
    right: list[Atom],
    cutoff: float,
    *,
    left_polar_only: bool = False,
    right_polar_only: bool = False,
) -> int:
    """Count atom pairs closer than cutoff (one pair per left atom, nearest)."""
    if left_polar_only:
        left = [a for a in left if a.is_polar]
    if right_polar_only:
        right = [a for a in right if a.is_polar]
    hit = 0
    for a in left:
        for b in right:
            if distance(a, b) <= cutoff:
                hit += 1
                break
    return hit


def clash_pairs(ligand: list[Atom], receptor: list[Atom], cutoff: float = 2.0) -> int:
    """Number of ligand heavy atoms with at least one receptor atom closer than cutoff."""
    return count_contacts(heavy(ligand), heavy(receptor), cutoff)


def site_overlap(
    ligand: list[Atom], reference: list[Atom], cutoff: float = 2.0
) -> float:
    ligand_heavy = heavy(ligand)
    reference_heavy = heavy(reference)
    if not ligand_heavy or not reference_heavy:
        return 0.0
    overlaps = count_contacts(ligand_heavy, reference_heavy, cutoff)
    return overlaps / len(ligand_heavy)


def catalytic_contacts(
    ligand: list[Atom],
    receptor: list[Atom],
    selection: list[dict],
    cutoff: float = 3.5,
) -> tuple[int, float | None]:
    """Contacts from ligand N/O to selected receptor atoms (e.g. Asp25 carboxylates)."""
    targets = select_atoms(receptor, selection)
    donors = [atom for atom in ligand if atom.is_polar]
    if not targets or not donors:
        return 0, None
    contacts = count_contacts(donors, targets, cutoff)
    nearest = min_distance(donors, targets)
    return contacts, nearest


def select_atoms(atoms: list[Atom], selection: list[dict]) -> list[Atom]:
    """selection = [{"resname": "ASP", "resseq": 25, "atoms": ["OD1","OD2"]}]"""
    chosen: list[Atom] = []
    for rule in selection:
        resname = str(rule.get("resname", "")).upper()
        resseq = rule.get("resseq")
        names = {str(name).upper() for name in rule.get("atoms", [])}
        for atom in atoms:
            if resname and atom.resname.upper() != resname:
                continue
            if resseq is not None and atom.resseq != int(resseq):
                continue
            if names and atom.name.upper() not in names:
                continue
            chosen.append(atom)
    return chosen
