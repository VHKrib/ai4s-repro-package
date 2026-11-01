"""Stage C: darunavir ligand preparation.

- co-crystal 017 (1T3R) coordinates + PubChem CID 213039 template graph
  -> cocrystal_pose.sdf   (heavy-atom reference pose for redock RMSD)
  -> darunavir_H.sdf      (explicit hydrogens)
  -> darunavir.pdbqt      (meeko mk_prepare_ligand: AutoDock types + torsion tree)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import gemmi
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds, rdMolDescriptors
from rdkit.Chem.inchi import MolToInchiKey

MK_LIGAND = _find_cli("mk_prepare_ligand")

pdb_path = Path(sys.argv[1])
tmpl_path = Path(sys.argv[2])
rundir = Path(sys.argv[3])
logdir = rundir / "logs"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------- crystal coordinates ----------
st = gemmi.read_structure(str(pdb_path))
lig_res = None
for chain in st[0]:
    for res in chain:
        if res.name == "017":
            lig_res = res
            break
    if lig_res is not None:
        break
if lig_res is None:
    raise SystemExit("ligand 017 not found")

names, elems, coords = [], [], []
for at in lig_res:
    names.append(at.name)
    elems.append(at.element.name[0].upper() + at.element.name[1:].lower())
    coords.append((at.pos.x, at.pos.y, at.pos.z))

rw = Chem.RWMol()
for e in elems:
    rw.AddAtom(Chem.Atom(e))
probe_conf = Chem.Conformer(rw.GetNumAtoms())
for i, p in enumerate(coords):
    probe_conf.SetAtomPosition(i, p)
probe = rw.GetMol()
probe.AddConformer(probe_conf, assignId=True)
rdDetermineBonds.DetermineConnectivity(probe)

# ---------- template (PubChem CID 213039, darunavir) ----------
tmpl = Chem.MolFromMolFile(str(tmpl_path), removeHs=True, sanitize=True)
if tmpl is None:
    raise SystemExit("cannot read template sdf")

params = Chem.AdjustQueryParameters()
params.makeBondsGeneric = True
query = Chem.AdjustQueryProperties(probe, params)
matches = tmpl.GetSubstructMatches(query, uniquify=False, maxMatches=1000)
if not matches:
    raise SystemExit("crystal connectivity does not match darunavir template")
order = list(matches[0])  # order[crystal_idx] = template_idx

pose = Chem.RWMol(tmpl)
conf = Chem.Conformer(pose.GetNumAtoms())
for crystal_idx, tmpl_idx in enumerate(order):
    conf.SetAtomPosition(tmpl_idx, coords[crystal_idx])
pose = pose.GetMol()
pose.RemoveAllConformers()
pose.AddConformer(conf, assignId=True)
Chem.SanitizeMol(pose)

formula = rdMolDescriptors.CalcMolFormula(pose)
key = MolToInchiKey(pose)
if formula != "C27H37N3O7S":
    raise SystemExit(f"formula mismatch: {formula}")

pose_path = rundir / "ligand" / "cocrystal_pose.sdf"
Chem.MolToMolFile(pose, str(pose_path))

# ---------- explicit hydrogens + meeko ----------
withH = Chem.AddHs(pose, addCoords=True)
h_path = rundir / "ligand" / "darunavir_H.sdf"
Chem.MolToMolFile(withH, str(h_path))

pdbqt = rundir / "ligand" / "darunavir.pdbqt"
cmd = [str(MK_LIGAND), "-i", str(h_path), "-o", str(pdbqt), "--rigid_macrocycles"]
proc = subprocess.run(cmd, capture_output=True, text=True)
(logdir / "04_ligand_prep.stdout.log").write_text(proc.stdout or "")
(logdir / "04_ligand_prep.stderr.log").write_text(proc.stderr or "")
if proc.returncode != 0 or not pdbqt.is_file():
    print(proc.stdout[-3000:], proc.stderr[-3000:])
    raise SystemExit(f"mk_prepare_ligand failed ({proc.returncode})")

ptext = pdbqt.read_text()
atom_lines = [l for l in ptext.splitlines() if l.startswith(("ATOM", "HETATM"))]
torsdof = [l.strip() for l in ptext.splitlines() if l.startswith("TORSDOF")]
branches = [l for l in ptext.splitlines() if l.startswith("BRANCH")]
from collections import Counter  # noqa: E402

ad_types = Counter(l[77:79].strip() for l in atom_lines)

payload = {
    "ligand_name": "darunavir",
    "pubchem_cid": 213039,
    "template_sdf": str(tmpl_path),
    "template_sdf_sha256": sha256(tmpl_path),
    "template_inchi_key": MolToInchiKey(tmpl),
    "bond_mapping": "crystal connectivity (RDKit DetermineConnectivity) graph-matched to PubChem template; "
                    "coordinates transferred onto template atom order; %d symmetry-equivalent matches, first used"
                    % len(matches),
    "pose_formula": formula,
    "pose_inchi_key": key,
    "pose_smiles": Chem.MolToSmiles(pose),
    "cocrystal_pose_sdf": str(pose_path),
    "cocrystal_pose_sdf_sha256": sha256(pose_path),
    "nr_heavy_atoms": pose.GetNumAtoms(),
    "withH_sdf": str(h_path),
    "withH_sdf_sha256": sha256(h_path),
    "total_atoms_with_H": withH.GetNumAtoms(),
    "ligand_pdbqt": str(pdbqt),
    "ligand_pdbqt_sha256": sha256(pdbqt),
    "pdbqt_atoms": len(atom_lines),
    "pdbqt_ad_types": dict(ad_types),
    "pdbqt_branches": len(branches),
    "torsdof": torsdof,
    "mk_prepare_ligand_command": cmd,
    "note": "ligand PDBQT was built from the co-crystal geometry, so Vina's RMSD l.b./u.b. "
            "columns are measured against the docked-start (= crystal) conformation.",
}
(rundir / "ligand" / "ligand_prep.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: payload[k] for k in ("pose_formula", "pose_inchi_key", "nr_heavy_atoms",
                                         "total_atoms_with_H", "pdbqt_atoms", "pdbqt_ad_types",
                                         "pdbqt_branches", "torsdof")}, ensure_ascii=False, indent=2))
print(proc.stdout[-800:])
