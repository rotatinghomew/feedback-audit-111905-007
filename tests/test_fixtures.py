import csv
from pathlib import Path
import unittest


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEADERS = {'attempts': ['case_id', 'round', 'sequence', 'channel', 'day'], 'audit_batches': ['id', 'hall', 'pool', 'channel', 'opening', 'closing', 'draw_size', 'trigger', 'predecessor', 'state', 'issued'], 'audit_capacity': ['hall', 'channel', 'opening', 'closing', 'contact_slots'], 'audit_orders': ['id', 'case_id', 'channel', 'inclusion_probability', 'issued'], 'batch_members': ['batch', 'case_id', 'issued'], 'batch_messages': ['delivery', 'receipt', 'batch', 'case_id', 'kind', 'occurred', 'received'], 'draws': ['case_id', 'inclusion_probability', 'assigned', 'assignment_probability', 'actual', 'score', 'cross_reason'], 'findings': ['case_id', 'step', 'status', 'day'], 'followup_draws': ['id', 'case_id', 'base_pool', 'conditional_probability', 'assigned', 'issued', 'completed', 'actual', 'score'], 'followup_limits': ['pool', 'max_attempts', 'total_budget'], 'frame': ['id', 'hall', 'step', 'channels', 'finished'], 'invites': ['case_id', 'channel', 'invited', 'score', 'responded'], 'permission_corrections': ['id', 'restriction', 'effective', 'recorded'], 'policy': ['min_confirmed', 'confidence', 'low_share_threshold', 'unknown_low_min', 'unknown_low_max', 'max_attempts', 'new_audit_budget'], 'restrictions': ['id', 'case_id', 'action', 'effective']}
LINKS = [('invites', 'case_id', 'frame', 'id'), ('draws', 'case_id', 'frame', 'id'), ('attempts', 'case_id', 'frame', 'id'), ('findings', 'case_id', 'frame', 'id'), ('audit_orders', 'case_id', 'frame', 'id'), ('batch_members', 'batch', 'audit_batches', 'id'), ('batch_members', 'case_id', 'frame', 'id'), ('batch_messages', 'batch', 'audit_batches', 'id'), ('batch_messages', 'case_id', 'frame', 'id'), ('permission_corrections', 'restriction', 'restrictions', 'id')]


def read(name):
    with (FIXTURES / (name + ".csv")).open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return reader.fieldnames, list(reader)


class SourceRecords(unittest.TestCase):
    def test_records_match_source_columns(self):
        for name, expected in HEADERS.items():
            with self.subTest(table=name):
                header, rows = read(name)
                self.assertEqual(header, expected)
                self.assertTrue(rows)
                self.assertTrue(all(None not in row and None not in row.values() for row in rows))
                if "id" in header and (name != "receipts" or "batch" not in header):
                    ids = [row["id"] for row in rows]
                    self.assertTrue(all(ids))
                    self.assertEqual(len(ids), len(set(ids)))

    def test_record_references_exist(self):
        for source, field, target, key in LINKS:
            with self.subTest(table=source, column=field):
                _, source_rows = read(source)
                _, target_rows = read(target)
                keys = {row[key] for row in target_rows}
                self.assertTrue(all(row[field] in keys for row in source_rows))
