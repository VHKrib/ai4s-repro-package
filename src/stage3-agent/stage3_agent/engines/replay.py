"""Replay engine: consume tidy pose tables produced elsewhere.

Used for the A1 regression case, where poses already exist under
`struct_case/results/`, and for any lab that runs docking on a cluster and
hands the results back as a table.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..intake import IntakeResult
from .base import EngineResult, ProgressCb


class ReplayEngine:
    name = "replay"

    def generate(
        self,
        config: Config,
        intake: IntakeResult,
        run_dir: Path,
        progress: ProgressCb | None = None,
    ) -> EngineResult:
        rows = [dict(row) for row in intake.poses]
        if progress:
            total = max(1, len(rows))
            for index, row in enumerate(rows, start=1):
                progress(
                    index,
                    total,
                    f"{row['candidate_id']}@{row['receptor_id']} pose {row['pose_index']}",
                )
        protocols = sorted({row["protocol"] for row in rows if row["protocol_role"] == "primary"})
        notes = [
            f"replay 引擎读取 {len(rows)} 个姿势，primary 协议 {protocols}",
            "本步骤不重新计算对接；数值来自输入表，可复现性由输入哈希保证",
        ]
        return EngineResult(rows=rows, engine=self.name, notes=notes)
