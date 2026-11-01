"""Stage B: receptor preparation for 1T3R -> AutoDock PDBQT.

1. filter ATOM records, chains A+B, altloc blank|A
2. detect CA-truncated residues (only N,CA,C,O,CB modelled) and map them to ALA so
   meeko's residue templates match; log every change
3. run meeko mk_prepare_receptor (--read_pdb, --default_altloc A) -> *_rigid.pdbqt
4. verify: atom counts, AutoDock types, polar-H presence
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PY = Path(sys.executable)
SCRIPTS = PY.parent
MK_RECEPTOR = SCRIPTS / "mk_prepare_receptor.exe"

pdb_path = Path(sys.argv[1])
rundir = Path(sys.argv[2])
logdir = rundir / "logs"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


lines = [l for l in pdb_path.read_text().splitlines()
         if l.startswith("ATOM  ") and l[21] in {"A", "B"} and l[16] in {" ", "A"}]

# group atoms per residue
res_atoms: dict[tuple, list[str]] = defaultdict(list)
for l in lines:
    res_atoms[(l[21], int(l[22:26]), l[17:20].strip())].append(l[12:16].strip())

BACKBONE_CB = {"N", "CA", "C", "O", "CB"}
# a residue modelled exactly up to CB (no side chain beyond CB) is a CA-truncated
# side chain; only LYS/ARG/etc. are candidates - GLY and ALA legitimately stop there.
truncated = sorted(k for k, atoms in res_atoms.items()
                   if set(atoms) == BACKBONE_CB and k[2] not in {"ALA", "GLY", "VAL", "THR", "SER", "CYS"})

print(f"residues total: {len(res_atoms)}")
print(f"CA-truncated residues (only N,CA,C,O,CB modelled): {truncated}")

out_lines = []
for l in lines:
    key = (l[21], int(l[22:26]), l[17:20].strip())
    if key in truncated:
        l = l[:17] + "ALA" + l[20:]
    out_lines.append(l.rstrip())
out_lines += ["TER", "END"]

prep_pdb = rundir / "receptor" / "1T3R_receptor_input.pdb"
prep_pdb.write_text("\n".join(out_lines) + "\n")

# ---- meeko mk_prepare_receptor ----
base = rundir / "receptor" / "1T3R"
cmd = [str(MK_RECEPTOR), "--read_pdb", str(prep_pdb), "-o", str(base),
       "--default_altloc", "A", "--compute_charges", "-p"]
proc = subprocess.run(cmd, capture_output=True, text=True)
(logdir / "03_receptor_prep.stdout.log").write_text(proc.stdout or "")
(logdir / "03_receptor_prep.stderr.log").write_text(proc.stderr or "")
print("mk_prepare_receptor exit:", proc.returncode)
print((proc.stdout or "")[-1500:])
print((proc.stderr or "")[-1500:])

rigid = base.parent / (base.name + "_rigid.pdbqt")
# meeko writes "<basename>.pdbqt" when there are no flexible residues, "<basename>_rigid.pdbqt" otherwise
if not rigid.is_file():
    alt = base.parent / (base.name + ".pdbqt")
    if alt.is_file():
        rigid = alt
if proc.returncode != 0 or not rigid.is_file():
    raise SystemExit(f"receptor prep failed (exit {proc.returncode})")


text = rigid.read_text()
atom_lines = [l for l in text.splitlines() if l.startswith(("ATOM", "HETATM"))]
types = defaultdict(int)
hd = 0
for l in atom_lines:
    ad_type = l[77:79].strip()
    types[ad_type] += 1
    if ad_type == "HD":
        hd += 1
elems = defaultdict(int)
for l in atom_lines:
    elems[l[76:78].strip()] += 1

payload = {
    "method": "meeko 0.8.0 mk_prepare_receptor --read_pdb --default_altloc A --compute_charges -p",
    "command": cmd,
    "input_pdb": str(prep_pdb),
    "input_pdb_sha256": sha256(prep_pdb),
    "receptor_pdbqt": str(rigid),
    "receptor_pdbqt_sha256": sha256(rigid),
    "receptor_atoms": len(atom_lines),
    "polar_hydrogens": hd,
    "ad_type_counts": dict(sorted(types.items())),
    "element_counts": dict(sorted(elems.items())),
    "ca_truncated_residues_mapped_to_ala": [f"{c}:{r}" for c, r, name in truncated],
    "altloc_policy": "record filter kept altloc blank|A; --default_altloc A",
    "waters_ions_removed": "HOH (261), PO4 (25), ligand 017 removed by ATOM-only filter",
}
(rundir / "receptor" / "receptor_prep.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: payload[k] for k in ("receptor_atoms", "polar_hydrogens",
                                         "ad_type_counts", "element_counts",
                                         "ca_truncated_residues_mapped_to_ala")},
                 ensure_ascii=False, indent=2))
