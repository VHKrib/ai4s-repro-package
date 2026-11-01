import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import stage3_fixture as fixture
from stage3_agent import pipeline
from stage3_agent.errors import Stage3Error
from stage3_agent.progress import render_status
from stage3_agent.state import RunState

OUTPUTS = [
    "candidates_ranked.csv",
    "decisions.csv",
    "reference_check.csv",
    "pose_metrics.csv",
    "receptor_matrix.csv",
    "uncertainties.csv",
]


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, **kwargs):
        return fixture.build_case(self.root, **kwargs)

    def run_case(self, config, run_id="r", **kwargs):
        state = pipeline.run(config, run_id=run_id, quiet=True, **kwargs)
        return state


class TestDeterminism(PipelineTestCase):
    def test_same_inputs_same_tables(self):
        config = self.build()
        first = self.run_case(config, run_id="a")
        second = self.run_case(config, run_id="b")
        for name in OUTPUTS:
            self.assertEqual(
                (first.run_dir / "outputs" / name).read_bytes(),
                (second.run_dir / "outputs" / name).read_bytes(),
                f"{name} differs between identical runs",
            )

    def test_manifest_hashes_match_files(self):
        config = self.build()
        state = self.run_case(config)
        manifest = json.loads((state.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("candidates_ranked", manifest["outputs"])
        self.assertIn("candidates_ranked_jsonl", manifest["outputs"])
        self.assertEqual(manifest["inputs"][0]["role"], "candidates")
        self.assertTrue(manifest["software"]["pipeline"].startswith("stage3/"))

    def test_validate_command_equivalent(self):
        config = self.build()
        state = self.run_case(config)
        manifest = json.loads((state.run_dir / "manifest.json").read_text(encoding="utf-8"))
        result = pipeline.validate_outputs(state.run_dir, manifest)
        self.assertEqual(result["errors"], [])
        self.assertGreaterEqual(len(result["checked"]), 7)


class TestReferenceGating(PipelineTestCase):
    def test_ranking_warning_does_not_block(self):
        config = self.build(expected_order=("c1", "ctrl"))
        state = self.run_case(config)
        self.assertEqual(state.payload["counters"]["reference_status"], "warn")
        rows = fixture.read_output(state.run_dir, "decisions.csv")
        candidates = {row["candidate_id"]: row for row in rows}
        self.assertEqual(candidates["c1"]["decision"], "advance_with_flags")
        self.assertIn("ranking_unvalidated", candidates["c1"]["decision_codes"])
        self.assertEqual(candidates["ctrl"]["decision"], "control_only")

    def test_failed_redock_forces_review(self):
        config = self.build(reference_rmsd=3.2)
        state = self.run_case(config)
        self.assertEqual(state.payload["counters"]["reference_status"], "fail")
        rows = fixture.read_output(state.run_dir, "decisions.csv")
        candidates = {row["candidate_id"]: row for row in rows}
        self.assertEqual(candidates["c1"]["decision"], "needs_review")
        self.assertEqual(candidates["c1"]["next_stage"], "review")
        self.assertIn("reference_check_failed", candidates["c1"]["hard_failures"])

    def test_af3_only_cannot_advance(self):
        config = self.build(engine="af3")
        state = self.run_case(config)
        ranked = fixture.read_output(state.run_dir, "candidates_ranked.csv")
        c1 = next(row for row in ranked if row["candidate_id"] == "c1")
        self.assertEqual(c1["affinity_kcal_mol"], "")
        decisions = {row["candidate_id"]: row for row in fixture.read_output(state.run_dir, "decisions.csv")}
        self.assertEqual(decisions["c1"]["decision"], "needs_review")
        self.assertIn("no_binding_evidence", decisions["c1"]["decision_codes"])


class TestFailureAndResume(PipelineTestCase):
    def test_contract_error_fails_stage_with_message(self):
        config = self.build()
        path = self.root / "inputs" / "candidates.csv"
        text = path.read_text(encoding="utf-8-sig").replace(",role,", ",kind,")
        path.write_text(text, encoding="utf-8-sig")
        with self.assertRaises(Stage3Error) as context:
            self.run_case(config)
        self.assertIn("role", str(context.exception))
        state = RunState.load(self.root / "runs" / "r")
        self.assertEqual(state.payload["status"], "failed")
        self.assertTrue(state.payload["errors"])

    def test_resume_skips_completed_stages(self):
        config = self.build()
        state = self.run_case(config)
        before = {
            stage["id"]: stage["started_at"] for stage in state.payload["stages"]
        }
        payload = state.payload
        payload["status"] = "interrupted"
        state.save(force=True)

        resumed = self.run_case(config, resume=True)
        self.assertEqual(resumed.payload["status"], "completed")
        after = {
            stage["id"]: stage["started_at"] for stage in resumed.payload["stages"]
        }
        for stage_id in ("intake", "applicability", "poses", "qc", "rank", "decide"):
            self.assertEqual(before[stage_id], after[stage_id], f"{stage_id} reran")

    def test_status_render_contains_progress(self):
        config = self.build()
        state = self.run_case(config)
        text = render_status(state)
        self.assertIn("STAGES", text)
        self.assertIn("candidates_ranked.csv", text)
        self.assertIn("progress:", text)


if __name__ == "__main__":
    unittest.main()
