"""One-click A4S (AI4ScienceHackthon) reproduction pipeline on Windows.

Two modes:

  case (default)  Run one packaged reproduction case end to end:
                    输入哈希校验 -> 受体/配体制备 -> 真实 Vina 对接 -> 重对接 RMSD
                    -> stage-3 打分/决策 -> validate -> 与期望数字逐项比对
                  Everything runs from the files in cases/<case_id>/, and the
                  verdict is OK only when every expected number is reproduced.

  jak2            The original 02 -> 03 chain: read a frozen module-02 delivery,
                  gate on READY_FOR_03, build stage-3 inputs, dock twice, redock,
                  validate. Needs a 02 delivery that is NOT shipped with this
                  package (third-party, ~217 MB); pass --stage02-delivery.

Examples
--------
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --case cases/hiv1_1t3r_darunavir --out runs/mycheck
    python scripts/run_pipeline.py --mode jak2 --stage02-delivery D:/a4s/stage02-delivery-v1.1.0

Honesty rules baked in: input hashes are recomputed, step exit codes are recorded
verbatim, steps that did not run are recorded as NOT_RUN (never as 0), and the
verdict is derived from the freshly produced artefacts -- not from a stored result.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
SCRIPTS = PKG / "scripts"
TOOLS = PKG / "tools"
VINA_EXE = TOOLS / "vina_1.2.5_win.exe"
STAGE3_DIR = PKG / "src" / "stage3-agent"
SHIM = SCRIPTS / "shim"
RUNS = PKG / "runs"

# Never leave __pycache__ inside a delivered package (it embeds absolute paths).
sys.dont_write_bytecode = True

RESULT_SCHEMA = "a4s.repro_result.v1"


# ---------------------------------------------------------------- utilities
def now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_python(env_name: str) -> Path:
    return PKG / "envs" / env_name / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run_step(name: str, cmd: list[str], cwd: Path, logdir: Path,
             env_extra: dict[str, str] | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    started = time.time()
    proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    elapsed = round(time.time() - started, 2)
    logdir.mkdir(parents=True, exist_ok=True)
    out_path = logdir / f"{name}.stdout.log"
    err_path = logdir / f"{name}.stderr.log"
    out_path.write_text(proc.stdout or "", encoding="utf-8")
    err_path.write_text(proc.stderr or "", encoding="utf-8")
    return {
        "step": name,
        "command": [str(c) for c in cmd],
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "elapsed_s": elapsed,
        "stdout_path": str(out_path),
        "stderr_path": str(err_path),
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def json_tail(text: str) -> dict:
    """Parse the last top-level JSON object printed by a step."""
    depth = 0
    start = None
    best = None
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    best = json.loads(text[start:index + 1])
                except ValueError:
                    pass
    if best is None:
        raise ValueError("no JSON object in step output")
    return best


def find_run_dir(text: str) -> Path:
    for line in text.splitlines():
        if line.strip().lower().startswith("run dir:"):
            return Path(line.split(":", 1)[1].strip())
    raise ValueError("stage3 output did not report a run dir")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def check(name: str, expected, actual, ok: bool, note: str = "") -> dict:
    return {"check": name, "expected": expected, "actual": actual, "pass": bool(ok), "note": note}


def close(a, b, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- case mode
def run_case(args) -> int:
    case_dir = Path(args.case).resolve()
    spec_path = case_dir / "case.json"
    if not spec_path.is_file():
        print(f"FATAL: no case.json under {case_dir}", file=sys.stderr)
        return 1
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    expected = spec["expected"]
    tol = spec.get("tolerances", {})
    tol_aff = float(tol.get("affinity_kcal_mol", 0.001))
    tol_rmsd = float(tol.get("rmsd_A", 0.02))
    tol_box = float(tol.get("box_A", 0.002))

    py_science = Path(args.science_python).resolve() if args.science_python else default_python("py3119-science")
    py_stage3 = Path(args.stage3_python).resolve() if args.stage3_python else default_python("py3119-stage3")

    run_dir = Path(args.out).resolve() if args.out else RUNS / f"{spec['case_id']}-{stamp()}"
    logs = run_dir / "logs"
    for sub in ("inputs", "ligand", "receptor", "docking", "evidence", "stage3_case", "logs"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    result: dict = {
        "schema_version": RESULT_SCHEMA,
        "mode": "case",
        "case_id": spec["case_id"],
        "pipeline_run_id": run_dir.name,
        "generated_at": now(),
        "host": {"platform": sys.platform, "python": sys.version.split()[0], "package_root": str(PKG)},
        "case_spec": str(spec_path),
        "steps": [],
        "checks": [],
        "limits": [
            "对接分不是实测亲和力；本例是共晶配体自对接（redocking）的方法自检，不是新抑制剂发现",
            "只做刚性受体 + 单一 vina 协议：无突变 panel、无 MD、无 AF3、无自由能计算",
        ],
    }

    def fatal(message: str, verdict: str) -> int:
        result["verdict"] = verdict
        print(f"FAILED: {message}", file=sys.stderr)
        dump(result, run_dir)
        summary(result, run_dir)
        return 1

    # ---- step 1: hash the packaged inputs --------------------------------
    bad = []
    for key, info in spec["inputs"].items():
        path = case_dir / info["path"]
        actual = sha256(path) if path.is_file() else None
        ok = actual == info["sha256"]
        result["checks"].append(check(f"input_sha256:{key}", info["sha256"], actual, ok))
        if not ok:
            bad.append(key)
    vina_info = spec["engine"]
    vina_actual = sha256(VINA_EXE) if VINA_EXE.is_file() else None
    result["checks"].append(check("vina_binary_sha256", vina_info["binary_sha256"], vina_actual,
                                  vina_actual == vina_info["binary_sha256"]))
    if bad or vina_actual != vina_info["binary_sha256"]:
        return fatal(f"input verification failed: {bad or 'vina binary'}", "BLOCKED_AT_INPUT_CHECK")
    for tool, label in ((py_science, "science python"), (py_stage3, "stage3 python")):
        if not tool.is_file():
            return fatal(f"{label} missing: {tool} (run setup_envs.ps1 first)", "BLOCKED_AT_ENVIRONMENT")

    pdb = case_dir / spec["inputs"]["pdb"]["path"]
    tmpl = case_dir / spec["inputs"]["ligand_template_sdf"]["path"]
    case_scripts = case_dir / "scripts"
    stage3_env = {"PYTHONPATH": str(SHIM)}

    # ---- step 2: crystal structure -> protein + co-crystal ligand + box --
    step = run_step("01_prep_inputs", [py_science, case_scripts / "prep_inputs.py", pdb, run_dir],
                    run_dir, logs)
    result["steps"].append(step_brief(step))
    if step["exit_code"] != 0:
        return fatal(f"prep_inputs exit {step['exit_code']}", "FAILED_AT_INPUT_PREP")
    prep = json.loads((run_dir / "inputs" / "prep_inputs.json").read_text(encoding="utf-8"))

    # ---- step 3: receptor preparation ------------------------------------
    step = run_step("02_prep_receptor", [py_science, case_scripts / "prep_receptor.py", pdb, run_dir],
                    run_dir, logs)
    result["steps"].append(step_brief(step))
    if step["exit_code"] != 0:
        return fatal(f"prep_receptor exit {step['exit_code']}", "FAILED_AT_RECEPTOR_PREP")
    rec = json.loads((run_dir / "receptor" / "receptor_prep.json").read_text(encoding="utf-8"))

    # ---- step 4: ligand preparation --------------------------------------
    step = run_step("03_prep_ligand", [py_science, case_scripts / "prep_ligand.py", pdb, tmpl, run_dir],
                    run_dir, logs)
    result["steps"].append(step_brief(step))
    if step["exit_code"] != 0:
        return fatal(f"prep_ligand exit {step['exit_code']}", "FAILED_AT_LIGAND_PREP")
    lig = json.loads((run_dir / "ligand" / "ligand_prep.json").read_text(encoding="utf-8"))

    # ---- step 5: real Vina docking ---------------------------------------
    center, size = prep["box_center"], prep["box_size"]
    step = run_step(
        "04_dock_vina",
        [py_science, case_scripts / "dock_vina.py", run_dir, rec["receptor_pdbqt"], lig["ligand_pdbqt"],
         *[f"{v}" for v in center], *[f"{v}" for v in size],
         vina_info["exhaustiveness"], vina_info["n_poses"], vina_info["seed"], vina_info["cpu"]],
        run_dir, logs)
    result["steps"].append(step_brief(step))
    if step["exit_code"] != 0:
        return fatal(f"dock_vina exit {step['exit_code']}", "FAILED_AT_DOCKING")
    dock = json.loads((run_dir / "docking" / "docking_result.json").read_text(encoding="utf-8"))

    # ---- step 6: independent redock RMSD ---------------------------------
    redock_json = run_dir / "evidence" / "redock_rmsd.json"
    step = run_step("05_redock_rmsd",
                    [py_science, SCRIPTS / "a4s_redock.py", "--poses", dock["poses_pdbqt"],
                     "--reference-sdf", lig["cocrystal_pose_sdf"], "--out", redock_json],
                    run_dir, logs)
    result["steps"].append(step_brief(step))
    if step["exit_code"] != 0 or not redock_json.is_file():
        return fatal(f"redock RMSD exit {step['exit_code']}", "FAILED_AT_REDOCK")
    redock = json.loads(redock_json.read_text(encoding="utf-8"))

    # ---- step 7: build stage-3 inputs ------------------------------------
    step = run_step("06_build_stage3_case",
                    [py_stage3, case_scripts / "build_stage3_case.py", "--run", run_dir,
                     "--case", spec_path, "--redock", redock_json], run_dir, logs)
    result["steps"].append(step_brief(step))
    if step["exit_code"] != 0:
        return fatal(f"build_stage3_case exit {step['exit_code']}", "FAILED_AT_STAGE3_INPUTS")
    cfg_path = Path(json_tail(step["stdout"])["config"])

    # ---- step 8: stage-3 run ---------------------------------------------
    step = run_step("07_stage3_run",
                    [py_stage3, "stage3.py", "run", "--config", cfg_path], STAGE3_DIR, logs, stage3_env)
    result["steps"].append(step_brief(step))
    if step["exit_code"] != 0:
        return fatal(f"stage3 run exit {step['exit_code']}", "FAILED_AT_STAGE3_RUN")
    s3_run = find_run_dir(step["stdout"])

    # ---- step 9: stage-3 validate ----------------------------------------
    step = run_step("08_stage3_validate",
                    [py_stage3, "stage3.py", "validate", "--run-id", s3_run.name, "--config", cfg_path],
                    STAGE3_DIR, logs, stage3_env)
    result["steps"].append(step_brief(step))
    validate_line = (step["stdout"] or step["stderr"]).strip().splitlines()[-1] \
        if (step["stdout"] or step["stderr"]).strip() else ""

    # ---- step 10: compare produced numbers with the expected ones --------
    affinities = [round(s["affinity_kcal_mol"], 3) for s in dock["scores"]]
    result["checks"] += [
        check("pose_count", expected["n_poses"], len(affinities), len(affinities) == expected["n_poses"]),
        check("affinities_by_pose", expected["affinities_kcal_mol"],
              affinities,
              all(close(a, b, tol_aff) for a, b in
                  zip(affinities, expected["affinities_kcal_mol"]))),
        check("best_affinity_kcal_mol", expected["best_affinity_kcal_mol"],
              min(affinities) if affinities else None,
              bool(affinities) and close(min(affinities), expected["best_affinity_kcal_mol"], tol_aff)),
        check("redock_best_rmsd_A", expected["redock_rmsd_A"],
              (redock.get("best_pose") or {}).get("rmsd_A"),
              bool(redock.get("best_pose")) and close(redock["best_pose"]["rmsd_A"],
                                                      expected["redock_rmsd_A"], tol_rmsd),
              f"threshold {expected['redock_threshold_A']} A"),
        check("redock_pose_index", expected["best_pose_index"],
              (redock.get("best_pose") or {}).get("pose_index"),
              (redock.get("best_pose") or {}).get("pose_index") == expected["best_pose_index"]),
        check("box_center", expected["box_center"], prep["box_center"],
              all(close(a, b, tol_box) for a, b in zip(prep["box_center"], expected["box_center"]))),
        check("box_size", expected["box_size"], prep["box_size"],
              all(close(a, b, tol_box) for a, b in zip(prep["box_size"], expected["box_size"]))),
        check("protein_atoms", expected["protein_atoms"], prep["protein_atoms"],
              prep["protein_atoms"] == expected["protein_atoms"]),
        check("receptor_pdbqt_atoms", expected["receptor_pdbqt_atoms"], rec["receptor_atoms"],
              rec["receptor_atoms"] == expected["receptor_pdbqt_atoms"]),
        check("ligand_heavy_atoms", expected["ligand_heavy_atoms"], lig["nr_heavy_atoms"],
              lig["nr_heavy_atoms"] == expected["ligand_heavy_atoms"]),
        check("ligand_formula", spec["ligand"]["formula"], lig["pose_formula"],
              lig["pose_formula"] == spec["ligand"]["formula"]),
        check("stage3_validate_exit", 0, step["exit_code"], step["exit_code"] == 0, validate_line),
    ]
    if step["exit_code"] != 0:
        return fatal("stage3 validate failed", "FAILED_AT_STAGE3_VALIDATE")

    # stage-3 outputs: pose count, best affinity, decision
    metrics = read_csv_rows(s3_run / "outputs" / "pose_metrics.csv")
    decisions = read_csv_rows(s3_run / "outputs" / "decisions.csv")
    s3_aff = [round(float(r["affinity_kcal_mol"]), 3) for r in metrics]
    result["checks"] += [
        check("stage3_pose_rows", expected["n_poses"], len(metrics), len(metrics) == expected["n_poses"]),
        check("stage3_best_affinity", expected["best_affinity_kcal_mol"], min(s3_aff) if s3_aff else None,
              bool(s3_aff) and close(min(s3_aff), expected["best_affinity_kcal_mol"], tol_aff)),
        check("stage3_decision", expected["stage3_decision"],
              (decisions[0].get("decision") if decisions else None),
              bool(decisions) and decisions[0].get("decision") == expected["stage3_decision"]),
        check("stage3_outputs_validated", expected["stage3_validate_outputs"],
              validate_line, bool(validate_line.startswith("OK"))),
    ]

    failed = [c for c in result["checks"] if not c["pass"]]
    result["verdict"] = "OK" if not failed else "MISMATCH"
    result["artifacts"] = {
        "stage3_run_dir": str(s3_run),
        "docking_result": str(run_dir / "docking" / "docking_result.json"),
        "redock_rmsd": str(redock_json),
        "stage3_config": str(cfg_path),
        "vina_log": dock["log_path"],
    }
    result["docking"] = {
        "engine": dock["vina_banner"],
        "binary_sha256": dock["vina_binary_sha256"],
        "elapsed_s": dock["elapsed_s"],
        "poses": dock["n_pose_models"],
        "affinities_kcal_mol": affinities,
        "command": dock["command"],
    }
    result["redock"] = {"method": redock["method"], "best_pose": redock["best_pose"],
                        "per_pose": redock["poses"]}
    result["stage3"] = {"run_dir": str(s3_run), "validate": validate_line,
                        "decisions": decisions, "poses": len(metrics)}
    result["finished_at"] = now()
    dump(result, run_dir)
    summary(result, run_dir)

    print("=" * 78)
    print(f"verdict            : {result['verdict']}")
    print(f"case               : {spec['case_id']}")
    print(f"poses / best score : {len(affinities)} / {min(affinities) if affinities else None} kcal/mol")
    print(f"redock RMSD        : {(redock.get('best_pose') or {}).get('rmsd_A')} A")
    print(f"stage3 validate    : {validate_line}")
    print(f"result.json        : {run_dir / 'result.json'}")
    print("=" * 78)
    if failed:
        print("mismatched checks:", file=sys.stderr)
        for c in failed:
            print(f"  - {c['check']}: expected {c['expected']!r}, got {c['actual']!r}", file=sys.stderr)
    return 0 if result["verdict"] == "OK" else 1


def step_brief(step: dict) -> dict:
    return {k: step[k] for k in ("step", "command", "cwd", "exit_code", "elapsed_s",
                                 "stdout_path", "stderr_path")}


def dump(result: dict, run_dir: Path) -> Path:
    path = run_dir / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def summary(result: dict, run_dir: Path) -> None:
    lines = [f"# {result['case_id']} 复现结果（{result['pipeline_run_id']}）", ""]
    lines.append(f"- 结论：{result.get('verdict')}")
    lines.append(f"- 生成时间：{result.get('generated_at')}")
    lines.append(f"- 步骤：{len(result.get('steps', []))}")
    lines.append("")
    lines.append("## 比对项")
    lines.append("")
    lines.append("| 检查 | 期望 | 实测 | 结果 |")
    lines.append("|---|---|---|---|")
    for c in result.get("checks", []):
        lines.append(f"| {c['check']} | `{c['expected']}` | `{c['actual']}` | "
                     f"{'PASS' if c['pass'] else 'FAIL'} |")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- jak2 mode
def run_jak2(args) -> int:
    """Original 02 -> 03 chain: a frozen 02 delivery is read, never modified."""
    delivery = Path(args.stage02_delivery).resolve() if args.stage02_delivery else None
    if delivery is None or not (delivery / "delivery_index.json").is_file():
        print("FATAL: --stage02-delivery must point at a module-02 delivery holding "
              "delivery_index.json (it is not shipped with this package).", file=sys.stderr)
        return 1
    os.environ["A4S_ROOT"] = str(PKG)
    os.environ["A4S_STAGE02_DELIVERY"] = str(delivery)
    os.environ["A4S_RUNS"] = str(Path(args.out).resolve() if args.out else RUNS)
    sys.path.insert(0, str(SCRIPTS))
    import pipeline_inputs as pi  # noqa: E402  (needs the env vars above)

    py_science = Path(args.science_python).resolve() if args.science_python else default_python("py3119-science")
    py_stage3 = Path(args.stage3_python).resolve() if args.stage3_python else default_python("py3119-stage3")
    py_host = Path(sys.executable)
    run_root = Path(os.environ["A4S_RUNS"])
    stamp_ = stamp()
    pipeline_id = f"pipeline-{stamp_}"
    evidence = run_root / pipeline_id
    logs = evidence / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    case_dir = run_root / "_stage3_jak2"

    for tool, label in ((py_host, "host python"), (py_stage3, "stage3 python"),
                        (py_science, "science python"), (VINA_EXE, "vina exe")):
        if not Path(tool).is_file():
            print(f"FATAL: {label} missing: {tool}", file=sys.stderr)
            return 1

    result: dict = {
        "schema_version": "a4s.pipeline_result.v1",
        "mode": "jak2",
        "pipeline_run_id": pipeline_id,
        "generated_at": now(),
        "host": {"platform": sys.platform, "python": sys.version.split()[0]},
        "stage02_delivery": str(delivery),
        "job_id": None,
        "stage02": {},
        "stage03": {},
        "limits": [
            "对接分不是实测亲和力；RXT 是 6VGL 共晶配体，本轮属 smoke test，不是新抑制剂发现",
            "只做了刚性受体 + 单一 vina 协议；无突变 panel、无 MD、无 AF3",
            "02 模块原批准环境是 macOS arm64 CPython3.11.9；本机 Windows 重跑为跨平台未验证组合",
        ],
    }

    step = run_step("01_stage02_check",
                    [py_host, "-S", "-B", delivery / "scripts" / "stage02.py", "check", "--target", "all"],
                    delivery, logs)
    payload = json_tail(step["stdout"]) if step["exit_code"] == 0 else {}
    result["stage02"]["check"] = {
        "command": " ".join(step["command"]), "exit_code": step["exit_code"],
        "status": payload.get("status"),
        "targets": [{"target": c.get("target"), "exit_code": c.get("exit_code"),
                     "status": (c.get("original_result") or {}).get("status")}
                    for c in payload.get("checks", [])],
        "elapsed_s": step["elapsed_s"], "log": step["stdout_path"],
    }
    if step["exit_code"] != 0 or payload.get("status") != "READONLY_CHECKS_PASS":
        result["verdict"] = "BLOCKED_AT_STAGE02_CHECK"
        dump_jak2(result, evidence)
        return 2

    locate = {}
    for target in ("JAK2", "JAK1"):
        step = run_step(f"02_stage02_locate_{target}",
                        [py_host, "-S", "-B", delivery / "scripts" / "stage02.py",
                         "locate", "--target", target], delivery, logs)
        payload = json_tail(step["stdout"]) if step["exit_code"] == 0 else {}
        entry = {"command": " ".join(step["command"]), "exit_code": step["exit_code"],
                 "job_id": payload.get("job_id"), "job_sha256": payload.get("job_sha256"),
                 "stage03_status": payload.get("stage03_status"), "artifacts": {}}
        for role, info in (payload.get("artifacts") or {}).items():
            path = Path(info["absolute_path"])
            actual = sha256(path) if path.is_file() else None
            entry["artifacts"][role] = {"path": info["absolute_path"], "sha256_declared": info["sha256"],
                                        "sha256_actual": actual, "match": actual == info["sha256"]}
        locate[target] = entry
    result["stage02"]["locate"] = locate
    mismatched = [f"{t}:{r}" for t, e in locate.items() for r, a in e["artifacts"].items() if not a["match"]]
    if locate["JAK2"]["exit_code"] != 0 or mismatched:
        result["verdict"] = "BLOCKED_AT_STAGE02_LOCATE"
        dump_jak2(result, evidence)
        return 2

    state = pi.collect_02_state()
    result["job_id"] = state["job"]["job_id"]
    gate = {
        "required": "READY_FOR_03", "declared": state["preparation_status"],
        "ready_for_03": state["ready_for_03"],
        "delivery_index_hash_crosscheck": state["delivery_index_crosscheck"],
        "passed": state["preparation_status"] == "READY_FOR_03" and state["ready_for_03"],
        "box": state["box"], "heavy_atoms_RXT": state["heavy_atoms"], "inputs_sha256": state["hashes"],
    }
    result["stage02"]["gate"] = gate
    if not gate["passed"]:
        result["verdict"] = "BLOCKED_BY_STAGE02_STATUS"
        dump_jak2(result, evidence)
        return 2

    result["stage02"]["rerun"] = {"status": "NOT_RUN",
                                  "reason": "编排未重跑 02（02 适配副本见 src/module02-portability-win）"}
    case_dir.mkdir(parents=True, exist_ok=True)
    candidates = pi.write_candidates(case_dir, state, "control")
    candidates_candidate = pi.write_candidates(case_dir, state, "candidate")
    receptors = pi.write_receptors(case_dir, state)
    config_v1 = pi.write_config(case_dir, candidates, receptors, "jak2_6vgl_rxt_dock")
    result["stage03"]["inputs"] = {"candidates_control": str(candidates),
                                   "candidates_candidate": str(candidates_candidate),
                                   "receptors": str(receptors), "config_v1": str(config_v1),
                                   "receptor_pdbqt": state["receptor_pdbqt"],
                                   "ligand_pdbqt": state["ligand_pdbqt"],
                                   "reference_sdf": state["reference_sdf"], "referenced_in_place": True}

    stage3_env = {"PYTHONPATH": str(SHIM)}
    step = run_step("03_stage3_run_v1",
                    [py_stage3, "stage3.py", "run", "--config", config_v1], STAGE3_DIR, logs, stage3_env)
    if step["exit_code"] != 0:
        result["verdict"] = "FAILED_AT_STAGE03_RUN1"
        result["stage03"]["run_v1"] = step_brief(step)
        dump_jak2(result, evidence)
        return 1
    run_v1 = find_run_dir(step["stdout"])
    poses_v1 = run_v1 / "poses" / "RXT" / "JAK2_6VGL" / "poses.pdbqt"
    result["stage03"]["run_v1"] = {"run_dir": str(run_v1), "exit_code": step["exit_code"],
                                   "elapsed_s": step["elapsed_s"], "poses_pdbqt": str(poses_v1)}

    redock_json = evidence / "redock_rmsd.json"
    step = run_step("04_redock_rmsd",
                    [py_science, SCRIPTS / "a4s_redock.py", "--poses", poses_v1,
                     "--reference-sdf", state["reference_sdf"], "--out", redock_json],
                    evidence, logs)
    if step["exit_code"] != 0 or not redock_json.is_file():
        result["verdict"] = "FAILED_AT_REDOCK_RMSD"
        dump_jak2(result, evidence)
        return 1
    redock = json.loads(redock_json.read_text(encoding="utf-8"))
    reference_poses = pi.write_reference_poses(case_dir, redock)
    config_v2 = pi.write_config(case_dir, candidates_candidate, receptors,
                                "jak2_6vgl_rxt_dock_final", reference_poses)
    result["stage03"]["redock"] = {"method": redock["method"], "best_pose": redock["best_pose"],
                                   "reference_poses_csv": str(reference_poses),
                                   "evidence_json": str(redock_json)}

    step = run_step("05_stage3_run_final",
                    [py_stage3, "stage3.py", "run", "--config", config_v2], STAGE3_DIR, logs, stage3_env)
    if step["exit_code"] != 0:
        result["verdict"] = "FAILED_AT_STAGE03_RUN_FINAL"
        dump_jak2(result, evidence)
        return 1
    run_final = find_run_dir(step["stdout"])
    metrics = read_csv_rows(run_final / "outputs" / "pose_metrics.csv")
    decisions = read_csv_rows(run_final / "outputs" / "decisions.csv")

    step_v = run_step("06_stage3_validate",
                      [py_stage3, "stage3.py", "validate", "--run-id", run_final.name,
                       "--config", config_v2], STAGE3_DIR, logs, stage3_env)
    best = min(metrics, key=lambda r: float(r["affinity_kcal_mol"])) if metrics else {}
    result["stage03"].update({
        "engine": {"name": "vina", "binary": str(VINA_EXE), "binary_sha256": sha256(VINA_EXE),
                   "parameters": {"exhaustiveness": 8, "n_poses": 9, "seed": 42, "cpu": 4,
                                  "protocol": "vina_rigid"}},
        "run_final": {"run_dir": str(run_final), "exit_code": step["exit_code"],
                      "elapsed_s": step["elapsed_s"], "poses_written": len(metrics),
                      "best_affinity_kcal_mol": float(best["affinity_kcal_mol"]) if best else None,
                      "decisions": decisions},
        "validate": {"exit_code": step_v["exit_code"],
                     "result": (step_v["stdout"] or step_v["stderr"]).strip().splitlines()[-1]
                     if (step_v["stdout"] or step_v["stderr"]).strip() else ""},
        "not_run": {"molecular_dynamics": "NOT_RUN", "af3_complex": "NOT_RUN",
                    "mutant_panel": "NOT_RUN", "flexible_sidechain_protocol": "NOT_RUN",
                    "developability_scores": "NOT_RUN", "sciencepal_orchestration": "NOT_RUN"},
    })
    result["verdict"] = "OK" if step_v["exit_code"] == 0 else "STAGE03_VALIDATE_FAILED"
    result["finished_at"] = now()
    dump_jak2(result, evidence)
    print("=" * 78)
    print(f"verdict      : {result['verdict']}")
    print(f"job_id       : {result['job_id']}")
    print(f"03 run dir   : {run_final}")
    print(f"03 best score: {result['stage03']['run_final']['best_affinity_kcal_mol']} kcal/mol")
    print(f"redock RMSD  : {result['stage03']['redock']['best_pose']['rmsd_A']} A")
    print(f"validate     : {result['stage03']['validate']['result']}")
    print(f"result.json  : {evidence / 'result.json'}")
    print("=" * 78)
    return 0 if result["verdict"] == "OK" else 1


def dump_jak2(result: dict, evidence: Path) -> None:
    (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")


# ---------------------------------------------------------------- entry point
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("case", "jak2"), default="case")
    parser.add_argument("--case", default=str(PKG / "cases" / "hiv1_1t3r_darunavir"),
                        help="case directory holding case.json (default: the packaged HIV-1 case)")
    parser.add_argument("--out", default=None, help="output directory (default: runs/<case>-<timestamp>)")
    parser.add_argument("--science-python", default=os.environ.get("A4S_SCIENCE_PYTHON"),
                        help="interpreter of the science env (rdkit/gemmi/meeko)")
    parser.add_argument("--stage3-python", default=os.environ.get("A4S_STAGE3_PYTHON"),
                        help="interpreter of the stage-3 env")
    parser.add_argument("--stage02-delivery", default=os.environ.get("A4S_STAGE02_DELIVERY"),
                        help="jak2 mode only: path to a frozen module-02 delivery")
    args = parser.parse_args()
    if args.mode == "jak2":
        return run_jak2(args)
    return run_case(args)


if __name__ == "__main__":
    sys.exit(main())
