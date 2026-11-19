import csv
import unittest
from pathlib import Path


class FixtureTests(unittest.TestCase):
    def test_feedback_data_distinguishes_invitation_and_response(self):
        path = Path(__file__).parents[1] / "fixtures" / "feedback.csv"
        with path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        types = {row["record_type"] for row in rows}
        self.assertTrue({"case", "invite", "response", "action"} <= types)
        self.assertEqual(len(rows), len({row["record_id"] for row in rows}))


if __name__ == "__main__":
    unittest.main()
