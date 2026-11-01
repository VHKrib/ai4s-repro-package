"""Command line interface for the stage-3 agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import schema as schema_module
from .config import load_config
from .errors import Stage3Error
from .io_utils import atomic_write_json, format_seconds
from .pipeline import run as run_pipeline
from .pipeline import validate_outputs
from .progress import follow, print_status
from .state import RunState, list_run_dirs, resolve_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stage3",
        description="阶段 03 计算建模、评估与排序 agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="运行完整管线")
    run_parser.add_argument("--config", required=True, help="配置文件路径")
    run_parser.add_argument("--run-id", default=None, help="指定 run id（默认按时间生成）")
    run_parser.add_argument("--resume", action="store_true", help="续跑最近的/指定的 run")
    run_parser.add_argument("--quiet", action="store_true", help="不打印进度")

    status_parser = sub.add_parser("status", help="查看进度")
    status_parser.add_argument("--run-id", default=None, help="run id（默认 latest）")
    status_parser.add_argument("--latest", action="store_const", const="latest", dest="run_id")
    status_parser.add_argument("--config", default=None, help="从配置解析 runs 目录")
    status_parser.add_argument("--runs-dir", default=None)
    status_parser.add_argument("--follow", action="store_true", help="持续刷新直到结束")
    status_parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    list_parser = sub.add_parser("list", help="列出所有 run")
    list_parser.add_argument("--config", default=None)
    list_parser.add_argument("--runs-dir", default=None)

    validate_parser = sub.add_parser("validate", help="复核产物列、哈希与 JSON Schema")
    validate_parser.add_argument("--run-id", default=None)
    validate_parser.add_argument("--latest", action="store_const", const="latest", dest="run_id")
    validate_parser.add_argument("--config", default=None)
    validate_parser.add_argument("--runs-dir", default=None)

    schemas_parser = sub.add_parser("schemas", help="导出 JSON Schema")
    schemas_parser.add_argument("--out", default="stage3_schemas")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "status":
            return _cmd_status(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "validate":
            return _cmd_validate(args)
        if args.command == "schemas":
            return _cmd_schemas(args)
    except Stage3Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


def _cmd_run(args) -> int:
    state = run_pipeline(
        args.config,
        run_id=args.run_id,
        resume=args.resume,
        quiet=args.quiet,
    )
    if not args.quiet:
        print(f"status: {state.payload['status']}")
    return 0 if state.payload["status"] == "completed" else 1


def _cmd_status(args) -> int:
    runs_dir = _runs_dir(args)
    run_dir = resolve_run(runs_dir, args.run_id)
    if args.follow:
        follow(run_dir)
    else:
        print_status(runs_dir, run_dir, as_json=args.json)
    return 0


def _cmd_list(args) -> int:
    runs_dir = _runs_dir(args)
    runs = list_run_dirs(runs_dir)
    if not runs:
        print(f"no runs under {runs_dir}")
        return 0
    print(f"{'run_id':<34}{'case':<18}{'status':<12}{'elapsed':<10}summary")
    for run_dir in runs:
        try:
            state = RunState.load(run_dir)
        except Stage3Error:
            continue
        payload = state.payload
        print(
            f"{payload['run_id']:<34}{payload['case_id']:<18}"
            f"{payload['status']:<12}{format_seconds(state.elapsed_total()):<10}"
            f"{payload.get('summary', '')}"
        )
    return 0


def _cmd_validate(args) -> int:
    run_dir = resolve_run(_runs_dir(args), args.run_id)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"error: no manifest at {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = validate_outputs(run_dir, manifest)
    if result["errors"]:
        print(f"FAILED  {len(result['errors'])} error(s)")
        for error in result["errors"]:
            print(f"  - {error}")
        return 1
    print(f"OK  {len(result['checked'])} outputs validated: {', '.join(result['checked'])}")
    return 0


def _cmd_schemas(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in schema_module.all_schemas().items():
        atomic_write_json(out / f"{name}.schema.json", payload)
    print(f"wrote {len(schema_module.all_schemas())} schemas to {out}")
    return 0


def _runs_dir(args) -> Path:
    if getattr(args, "runs_dir", None):
        return Path(args.runs_dir).resolve()
    config_path = getattr(args, "config", None)
    if config_path:
        return load_config(config_path).runs_dir
    return (Path.cwd() / "stage3_runs").resolve()


if __name__ == "__main__":
    raise SystemExit(main())
