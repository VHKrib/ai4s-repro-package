"""Convert AlphaFold 3 output files into a stage-3 `af3` engine manifest CSV.

Usage:
  python af3_to_stage3_manifest.py \
      --af3-out  <dir containing *_model.cif + *_summary_confidences.json> \
      --map      pairs.csv            (columns: candidate_id,receptor_id,af3_name) \
      --out      03_af3_manifest.csv  (stage-3 manifest) \
      [--copy-to <case inputs dir>]    copy the .cif files next to the manifest

stage-3 af3 adapter contract (stage3_agent/engines/af3.py):
  required columns : candidate_id, receptor_id, structure_path
  optional columns : ranking_score, iptm, ptm, chain_pair_iptm, notes
  structure_path   : resolved relative to the stage-3 case root (parent of the
                     directory holding the case config.json)
  candidate_id     : MUST exist in inputs/candidates.csv (unknown ids raise ContractError)
  confidence       : first non-empty of ranking_score / iptm / ptm (float)
  .pdb structures  : trigger an atom parse (coarse clash sanity); .cif does not.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path


def load_conf(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten_matrix(value):
    if isinstance(value, list) and value and isinstance(value[0], list):
        return ";".join(str(v) for row in value for v in row)
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    return "" if value is None else str(value)


def find_af3_files(af3_out: Path, af3_name: str) -> tuple[Path, Path]:
    """AF3 writes <name>_model.cif and <name>_summary_confidences.json.

    Also accepts the generic names model.cif / summary_confidences.json, which is
    what a prompt-contract hand-off directory usually contains.
    """
    cif = af3_out / f"{af3_name}_model.cif"
    conf = af3_out / f"{af3_name}_summary_confidences.json"
    if not cif.exists():
        cands = sorted(af3_out.rglob("*_model.cif"))
        cif = next((c for c in cands if af3_name and af3_name in c.name), None) \
            or (cands[0] if cands else same_dir(af3_out, "model.cif"))
    if not conf.exists():
        cands = sorted(af3_out.rglob("*_summary_confidences.json"))
        conf = next((c for c in cands if af3_name and af3_name in c.name), None) \
            or (cands[0] if cands else same_dir(af3_out, "summary_confidences.json"))
    if cif is None or not cif.exists():
        raise SystemExit(f"no *_model.cif (or model.cif) under {af3_out}")
    if conf is None or not conf.exists():
        raise SystemExit(f"no *_summary_confidences.json (or summary_confidences.json) under {af3_out}")
    return cif, conf


def same_dir(d: Path, name: str) -> Path | None:
    p = d / name
    return p if p.exists() else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--af3-out", required=True)
    ap.add_argument("--map", required=True, help="CSV: candidate_id,receptor_id,af3_name")
    ap.add_argument("--out", required=True)
    ap.add_argument("--copy-to", default=None, help="case inputs dir to copy .cif into")
    ap.add_argument("--notes-prefix", default="af3")
    ns = ap.parse_args(argv)

    af3_out = Path(ns.af3_out)
    out_path = Path(ns.out)
    copy_dir = Path(ns.copy_to) if ns.copy_to else out_path.parent
    copy_dir.mkdir(parents=True, exist_ok=True)

    with open(ns.map, encoding="utf-8-sig", newline="") as fh:
        pairs = list(csv.DictReader(fh))

    rows = []
    for p in pairs:
        cif, conf_path = find_af3_files(af3_out, p.get("af3_name", "").strip())
        conf = load_conf(conf_path)
        dst = copy_dir / f"{p['candidate_id']}__{p['receptor_id']}__af3.cif"
        shutil.copy2(cif, dst)
        rows.append({
            "candidate_id": p["candidate_id"],
            "receptor_id": p["receptor_id"],
            "structure_path": str(dst).replace("\\", "/"),
            "ranking_score": conf.get("ranking_score", ""),
            "iptm": conf.get("iptm", ""),
            "ptm": conf.get("ptm", ""),
            "chain_pair_iptm": flatten_matrix(conf.get("chain_pair_iptm")),
            "notes": f"{ns.notes_prefix}:{cif.name}",
        })

    cols = ["candidate_id", "receptor_id", "structure_path", "ranking_score",
            "iptm", "ptm", "chain_pair_iptm", "notes"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out_path} ({len(rows)} rows)")
    for r in rows:
        print("  ", r["candidate_id"], r["receptor_id"], "score=", r["ranking_score"],
              "iptm=", r["iptm"], "ptm=", r["ptm"], "->", r["structure_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
