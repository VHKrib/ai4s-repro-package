"""Stage A: 1T3R -> protein (chains A,B) + co-crystal ligand 017 -> SDF/PDBQT inputs, box computation.

Run with envs\\py3119-science\\python.exe (rdkit 2026.9.1 / gemmi 0.7.5 / meeko 0.8.0).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import gemmi
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

pdb_path = Path(sys.argv[1])
rundir = Path(sys.argv[2])

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

# ---------------- protein: chains A,B, ATOM only, altloc A ----------------
st = gemmi.read_structure(str(pdb_path))
st.setup_entities()
st.remove_waters()
st.remove_alternative_conformations()
st.remove_hydrogens()

prot_path = rundir / "receptor" / "1T3R_protein_AB.pdb"
# record-level filtering keeps original numbering/atom names -> auditable against the RCSB file
kept: list[str] = []
for line in pdb_path.read_text().splitlines():
    if not line.startswith("ATOM  "):
        continue
    if line[21] not in {"A", "B"}:
        continue
    if line[16] not in {" ", "A"}:
        continue
    kept.append(line)
kept.append("TER")
kept.append("END")
prot_path.write_text("\n".join(kept) + "\n")
n_atoms = sum(1 for l in kept if l.startswith("ATOM"))
chain_atoms = {c: sum(1 for l in kept if l.startswith("ATOM") and l[21] == c) for c in ("A", "B")}
residues = sorted({(l[21], int(l[22:26])) for l in kept if l.startswith("ATOM")})
chain_res = {c: len({r for ch, r in residues if ch == c}) for c in ("A", "B")}
# verify with gemmi that nothing odd remains
chk = gemmi.read_structure(str(prot_path))[0]
gemmi_atoms = sum(1 for _ in chk.all())
if gemmi_atoms != n_atoms:
    raise SystemExit(f"gemmi re-read mismatch: {gemmi_atoms} vs {n_atoms}")


# ---------------- ligand 017 -> pdb -> rdkit mol ----------------
st2 = gemmi.read_structure(str(pdb_path))
model2 = st2[0]
lig_res = None
for chain in model2:
    for res in chain:
        if res.name == "017":
            lig_res = res
            break
    if lig_res is not None:
        break
if lig_res is None:
    raise SystemExit("ligand 017 not found")

lig_lines = []
serial = 1
for at in lig_res:
    element = at.element.name.upper()
    name = at.name
    # PDB columns: 1-6 record, 7-11 serial, 13-16 name, 18-20 resName, 22 chainID, 23-26 resSeq,
    # 31-38 x, 39-46 y, 47-54 z, 55-60 occ, 61-66 B, 77-78 element
    lig_lines.append(
        f"HETATM{serial:5d} {name:<4s} {lig_res.name:>3s} A{1200:4d}    "
        f"{at.pos.x:8.3f}{at.pos.y:8.3f}{at.pos.z:8.3f}  1.00 10.00          {element:>2s}"
    )
    serial += 1
lig_lines.append("END")
lig_pdb = rundir / "ligand" / "017_1T3R_cocrystal_raw.pdb"
lig_pdb.write_text("\n".join(lig_lines) + "\n")

lig_block = "\n".join(lig_lines)
mol = Chem.MolFromPDBBlock(lig_block, sanitize=False, removeHs=False)
if mol is None:
    raise SystemExit("RDKit could not parse ligand pdb block")

# bond perception fallback: proximity bonding is what produced mol; fix valences
try:
    Chem.SanitizeMol(mol)
    sanitize_msg = "SANITIZE_OK"
except Exception as exc:  # noqa: BLE001
    sanitize_msg = f"SANITIZE_FAIL: {exc}"
    # retry from scratch and let RDKit re-derive bonds+rings, then relax
    mol = Chem.MolFromPDBBlock(lig_block, sanitize=False, removeHs=False, proximityBonding=True)
    mol.UpdatePropertyCache(strict=False)
    Chem.SanitizeMol(mol, Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_PROPERTIES)

formula_heavy = rdMolDescriptors.CalcMolFormula(mol)
mol_noH = Chem.RemoveHs(mol)
from rdkit.Chem.Descriptors import MolWt  # noqa: E402

heavy_atoms = mol_noH.GetNumAtoms()
ref_path = rundir / "ligand" / "017_1T3R_cocrystal.sdf"
Chem.MolToMolFile(mol, str(ref_path))

# ---------------- box from co-crystal ligand heavy atoms (AABB + 5.0 padding) ----------
conf = mol.GetConformer()
xs, ys, zs = [], [], []
for i in range(mol.GetNumAtoms()):
    p = conf.GetAtomPosition(i)
    if mol.GetAtomWithIdx(i).GetSymbol() == "H":
        continue
    xs.append(p.x); ys.append(p.y); zs.append(p.z)

minx, maxx = min(xs), max(xs)
miny, maxy = min(ys), max(ys)
minz, maxz = min(zs), max(zs)
padding = 5.0
size = [round((maxx - minx) + 2 * padding, 3),
        round((maxy - miny) + 2 * padding, 3),
        round((maxz - minz) + 2 * padding, 3)]
center = [round((minx + maxx) / 2, 3), round((miny + maxy) / 2, 3), round((minz + maxz) / 2, 3)]
centroid = [round(sum(xs) / len(xs), 3), round(sum(ys) / len(ys), 3), round(sum(zs) / len(zs), 3)]

payload = {
    "pdb_id": "1T3R",
    "pdb_source": "RCSB https://files.rcsb.org/download/1T3R.pdb",
    "protein_file": str(prot_path),
    "protein_sha256": sha256(prot_path),
    "chains": ["A", "B"],
    "protein_atoms": n_atoms,
    "protein_chain_atoms": chain_atoms,
    "protein_chain_residues": chain_res,
    "protein_prep": "record-level filter: ATOM only (drops HOH/PO4/ligand HETATM), chains A+B, altloc blank|A",
    "ligand_resname": "017",
    "ligand_raw_pdb": str(lig_pdb),
    "ligand_sdf": str(ref_path),
    "ligand_sdf_sha256": sha256(ref_path),
    "ligand_formula_from_pdb_block": formula_heavy,
    "ligand_heavy_atoms": heavy_atoms,
    "ligand_sanitize": sanitize_msg,
    "ligand_molwt": round(MolWt(mol_noH), 3),
    "box_method": "co-crystal ligand heavy-atom AABB + 5.0 A padding per axis (JAK2 case method)",
    "padding_A": padding,
    "ligand_aabb": {"min": [round(minx, 3), round(miny, 3), round(minz, 3)],
                    "max": [round(maxx, 3), round(maxy, 3), round(maxz, 3)],
                    "extent": [round(maxx - minx, 3), round(maxy - miny, 3), round(maxz - minz, 3)]},
    "ligand_centroid": centroid,
    "box_center": center,
    "box_size": size,
}
out = rundir / "inputs" / "prep_inputs.json"
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
