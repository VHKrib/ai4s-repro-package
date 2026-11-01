import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stage3_agent import schema
from stage3_agent.errors import ContractError


class TestSchemaContract(unittest.TestCase):
    def test_column_order_is_exact(self):
        spec = schema.CANDIDATES_RANKED
        schema.validate_columns(spec, spec.column_names)
        with self.assertRaises(ContractError):
            schema.validate_columns(spec, list(reversed(spec.column_names)))

    def test_missing_column_rejected(self):
        spec = schema.DECISIONS
        columns = [name for name in spec.column_names if name != "decision"]
        with self.assertRaises(ContractError):
            schema.validate_columns(spec, columns)

    def test_float_is_rounded_and_validated(self):
        col = schema.Col("score", "float", (), 0, 1)
        self.assertEqual(schema.coerce_value(col, 0.123456), "0.123")
        with self.assertRaises(ContractError):
            schema.coerce_value(col, 1.5)

    def test_enum_rejects_unknown(self):
        col = schema.Col("decision", "enum", ("advance", "revise"))
        self.assertEqual(schema.coerce_value(col, "revise"), "revise")
        with self.assertRaises(ContractError):
            schema.coerce_value(col, "maybe")

    def test_required_blank_rejected(self):
        col = schema.Col("candidate_id", "str", (), None, None, False)
        with self.assertRaises(ContractError):
            schema.coerce_value(col, "")

    def test_json_schema_has_all_columns(self):
        spec = schema.POSE_METRICS
        payload = schema.json_schema(spec)
        properties = payload["items"]["properties"]
        self.assertEqual(list(properties), spec.column_names)
        self.assertIn("pose_score", properties)

    def test_normalize_row_rejects_extra(self):
        row = {name: "" for name in schema.UNCERTAINTIES.column_names}
        row["uncertainty_id"] = "U-1"
        row["scope"] = "case"
        row["kind"] = "test"
        row["statement"] = "x"
        row["impact"] = "note"
        row["status"] = "open"
        self.assertEqual(
            schema.normalize_row(schema.UNCERTAINTIES, row)["uncertainty_id"], "U-1"
        )
        row["extra"] = "nope"
        with self.assertRaises(ContractError):
            schema.normalize_row(schema.UNCERTAINTIES, row)


if __name__ == "__main__":
    unittest.main()
