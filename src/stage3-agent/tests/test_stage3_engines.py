import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stage3_agent.config import load_config
from stage3_agent.engines.af3 import AF3Engine
from stage3_agent.engines.replay import ReplayEngine
from stage3_agent.engines.vina import VinaEngine
from stage3_agent.errors import EngineUnavailable
from stage3_agent.intake import IntakeResult


def pdb_atom(serial, name, resname, resseq, x, y, z, chain="A"):
    return (
        f"ATOM  {serial:>5} {name:<4}{'':1}{resname:>3} {chain}{resseq:>4}    "
        f"{x:>8.3f}{y:>8.3f}{z:>8.3f}  1.00  0.00           C\n"
    )


def pdbqt_atom(serial, name, x, y, z, atom_type):
    return (
        f"ATOM  {serial:>5} {name:<4}{'':1}{'LIG':>3} {'A'}{1:>4}    "
        f"{x:>8.3f}{y:>8.3f}{z:>8.3f}  1.00  0.00     0.000 {atom_type:<2}\n"
    )


class FakeVina:
    def __init__(self, **kwargs):
        self.path = None

    def set_receptor(self, path):
        self.receptor = path

    def set_ligand_from_file(self, path):
        self.ligand = path

    def compute_vina_maps(self, center, box_size):
        self.center = center
        self.box = box_size

    def dock(self, exhaustiveness, n_poses):
        self.n_poses = n_poses

    def write_poses(self, path, n_poses, overwrite=True):
        models = []
        for index in (1, 2):
            models.append(
                "MODEL %d\n" % index
                + pdbqt_atom(1, "C", 10.0, 10.0, 10.0, "C")
                + pdbqt_atom(2, "O", 11.0, 10.0, 10.0, "OA")
                + "ENDMDL\n"
            )
        Path(path).write_text("".join(models), encoding="utf-8")

    def energies(self, n_poses):
        return [[-8.0, 0.0, 0.0, 0.0], [-7.5, 0.0, 0.0, 0.0]][:n_poses]


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "case").mkdir()
        (self.root / "inputs").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self, engine="vina", options=None):
        payload = {
            "schema_version": "stage3-config/1",
            "case_id": "engine-test",
            "inputs": {
                "candidates": "inputs/candidates.csv",
                "receptors": "inputs/receptors.csv",
                "poses": "inputs/poses.csv",
            },
            "engine": {"name": engine, "options": options or {}},
            "runs_dir": "runs",
        }
        path = self.root / "case" / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_config(path)


def make_intake(root: Path) -> IntakeResult:
    receptor_pdb = root / "inputs" / "receptor.pdb"
    receptor_pdb.write_text(
        pdb_atom(1, "OD1", "ASP", 25, 11.0, 10.0, 13.4)
        + pdb_atom(2, "OD2", "ASP", 25, 12.0, 10.0, 13.4),
        encoding="utf-8",
    )
    receptor_pdbqt = root / "inputs" / "receptor.pdbqt"
    receptor_pdbqt.write_text("", encoding="utf-8")
    ligand = root / "inputs" / "ligand.pdbqt"
    ligand.write_text("", encoding="utf-8")
    candidates = [
        {
            "candidate_id": "c1",
            "name": "C1",
            "role": "candidate",
            "smiles": "",
            "source": "",
            "chemotype": "",
            "heavy_atoms": "",
            "ligand_sdf_path": "",
            "ligand_pdbqt_path": "inputs/ligand.pdbqt",
            "availability": "",
            "synthesizability": "",
            "solubility_risk": "",
            "selectivity_risk": "",
            "notes": "",
        }
    ]
    receptors = [
        {
            "receptor_id": "WT",
            "label": "WT",
            "mutations": "",
            "pdb_path": "inputs/receptor.pdb",
            "pdbqt_path": "inputs/receptor.pdbqt",
            "flex_pdbqt_path": "",
            "box_center_x": "11.0",
            "box_center_y": "10.0",
            "box_center_z": "11.0",
            "box_size_x": "20.0",
            "box_size_y": "20.0",
            "box_size_z": "20.0",
            "is_primary": "true",
        }
    ]
    return IntakeResult(
        candidates=candidates,
        receptors=receptors,
        poses=[],
        panel=[],
        reference_poses=[],
        input_info={},
        normalized_paths={},
        warnings=[],
        primary_receptor="WT",
    )


class TestVinaAdapter(EngineTestCase):
    def test_dock_pair_produces_metrics(self):
        fake = types.ModuleType("vina")
        fake.Vina = FakeVina
        sys.modules["vina"] = fake
        try:
            config = self._config(
                options={
                    "catalytic_selection": [
                        {"resname": "ASP", "resseq": 25, "atoms": ["OD1", "OD2"]}
                    ],
                    "exhaustiveness": 4,
                    "n_poses": 2,
                }
            )
            intake = make_intake(self.root)
            result = VinaEngine().generate(config, intake, self.root / "run")
        finally:
            sys.modules.pop("vina", None)

        self.assertEqual(len(result.rows), 2)
        first = result.rows[0]
        self.assertEqual(first["engine"], "vina")
        self.assertEqual(first["affinity_kcal_mol"], -8.0)
        self.assertEqual(first["heavy_atoms"], 2)
        self.assertEqual(first["ligand_efficiency"], 4.0)
        self.assertEqual(first["asp25_contacts_under_3.5A"], 1)
        self.assertEqual(first["clash_pairs"], 0)
        self.assertEqual(first["geometry_source"], "sdf")
        self.assertTrue((self.root / "run" / "poses" / "c1" / "WT" / "poses.pdbqt").exists())

    @unittest.skipIf(importlib.util.find_spec("vina") is not None, "vina installed")
    def test_missing_vina_raises_clear_error(self):
        config = self._config()
        intake = make_intake(self.root)
        with self.assertRaises(EngineUnavailable) as context:
            VinaEngine().generate(config, intake, self.root / "run")
        self.assertIn("pip install vina", str(context.exception))


class TestAf3Adapter(EngineTestCase):
    def test_confidence_only_rows(self):
        config = self._config(
            engine="af3", options={"manifest": "inputs/af3_manifest.csv"}
        )
        (self.root / "inputs" / "model.pdb").write_text(
            pdb_atom(1, "CA", "ALA", 1, 10.0, 10.0, 10.0), encoding="utf-8"
        )
        (self.root / "inputs" / "af3_manifest.csv").write_text(
            "candidate_id,receptor_id,structure_path,ranking_score,iptm,ptm,chain_pair_iptm,notes\n"
            "c1,WT,inputs/model.pdb,0.82,0.75,0.70,0.60,\n",
            encoding="utf-8-sig",
        )
        intake = make_intake(self.root)
        result = AF3Engine().generate(config, intake, self.root / "run")
        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertIsNone(row["affinity_kcal_mol"])
        self.assertEqual(row["confidence_score"], 0.82)
        self.assertEqual(row["confidence_type"], "ranking_score")
        self.assertEqual(result.uncertainties[0]["impact"], "blocks_advance")


class TestReplayAdapter(EngineTestCase):
    def test_passthrough(self):
        config = self._config(engine="replay")
        intake = make_intake(self.root)
        intake.poses = [
            {
                "candidate_id": "c1",
                "receptor_id": "WT",
                "pose_index": "1",
                "protocol": "vina_flex",
                "protocol_role": "primary",
                "engine": "replay",
            }
        ]
        result = ReplayEngine().generate(config, intake, self.root / "run")
        self.assertEqual(result.rows, intake.poses)


if __name__ == "__main__":
    unittest.main()
