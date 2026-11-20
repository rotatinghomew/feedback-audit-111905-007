"""Behavioural tests for the audit rules.

These pin the traps the source records were built around.  A fixture edit that
quietly re-opens a route, grants a reviewer credit they did not earn, or lets a
shared step count twice will fail here.
"""

import csv
import unittest
from pathlib import Path

from audit import model, report, rules

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class Loaded(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = model.load(FIXTURES)
        cls.verdicts = rules.all_unit_verdicts(cls.data)
        cls.routes = rules.all_route_verdicts(cls.data, cls.verdicts)


class TestBlindnessAndStanding(Loaded):
    def test_seen_gold_control_earns_no_standing(self):
        # J07 looked at the gold standard for G3; it must not help D qualify.
        review = self.data.reviews["J07"]
        self.assertEqual(review.seen_gold, "yes")
        self.assertFalse(rules.review_use(self.data, review).usable)
        usable_controls = {r.id for r in self.data.reviews_by_control.get("G3", []) if rules.review_use(self.data, r).usable}
        self.assertNotIn("J07", usable_controls)
        self.assertEqual(rules.qualification_time(self.data, "D", "en", "proof"), "09:38")

    def test_seen_gold_translation_review_is_excluded(self):
        # No fixture row combines translation + seen_gold, so build one from a
        # known-good judgement and confirm the state machine rejects it.
        original = self.data.reviews["J35"]
        tainted = model.Review(
            id="JXX",
            reviewer=original.reviewer,
            translation=original.translation,
            control="",
            decision=original.decision,
            reason=original.reason,
            seen_gold="yes",
            at=original.at,
        )
        use = rules.review_use(self.data, tainted)
        self.assertFalse(use.usable)
        self.assertEqual(use.state, "seen_gold")

    def test_control_answers_are_not_translation_evidence(self):
        for rid in ("J01", "J27", "J31"):
            self.assertFalse(rules.review_use(self.data, self.data.reviews[rid]).usable)

    def test_reviewer_with_one_sided_pairs_cannot_support_high_risk(self):
        # B waves through both eligible pairs, one of which is a real difference.
        standing = rules.control_standing(self.data, "B", "en", "eligible")
        self.assertFalse(standing.usable)
        self.assertGreater(standing.wrong, 0)

    def test_later_qualification_does_not_license_earlier_score(self):
        # D only earns en/proof pair standing after J31/J32 at 09:38; J19 at
        # 08:31 predates it and must not be counted.
        use = rules.review_use(self.data, self.data.reviews["J19"])
        self.assertFalse(use.usable)
        self.assertEqual(use.state, "no_standing")
        self.assertEqual(rules.qualification_time(self.data, "D", "en", "proof"), "09:38")

    def test_later_judgement_after_qualification_counts(self):
        for rid in ("J26", "J34"):
            self.assertTrue(rules.review_use(self.data, self.data.reviews[rid]).usable)

    def test_low_risk_does_not_need_pair_standing(self):
        self.assertFalse(rules.control_standing(self.data, "B", "en", "end").usable)
        self.assertTrue(rules.reviewer_licensed(self.data, "B", "en", "end", "low", "08:45"))


class TestUnitVerdicts(Loaded):
    def test_high_risk_needs_two_distinct_reviewers(self):
        # T4 has one qualified blind reviewer (J35).
        self.assertEqual(self.verdicts["T4"].effective_status, rules.NOT_INDEPENDENT)
        self.assertEqual(self.verdicts["T4"].distinct_support, 1)

    def test_two_distinct_reviewers_support(self):
        self.assertEqual(self.verdicts["T20"].effective_status, rules.SUPPORTED)
        self.assertEqual(self.verdicts["T20"].distinct_support, 2)

    def test_opposed_by_qualified_reviewer_is_content_gap(self):
        # A judged T6 "different"; B's agreement is excluded (no standing).
        self.assertEqual(self.verdicts["T6"].evidence_status, rules.CONTENT_GAP)
        self.assertFalse(self.verdicts["T6"].usable_for_new_navigation)

    def test_ru_proof_gap_is_real(self):
        self.assertEqual(self.verdicts["T14"].effective_status, rules.CONTENT_GAP)
        self.assertIn(self.data.reviews["J21"], self.verdicts["T14"].oppose)

    def test_unreviewed_units_are_not_counted_as_passed(self):
        for tid in ("T3", "T11", "T12", "T15", "T18", "T19"):
            self.assertEqual(self.verdicts[tid].effective_status, rules.UNVERIFIED)
            self.assertFalse(self.verdicts[tid].usable_for_new_navigation)

    def test_correction_supersedes_even_a_supported_translation(self):
        # T2 is supported by A but its source unit U2 was corrected to U9.
        self.assertEqual(self.verdicts["T2"].effective_status, rules.SUPERSEDED)
        self.assertFalse(self.verdicts["T2"].usable_for_new_navigation)
        self.assertEqual(self.verdicts["T2"].superseded_by, "U9")


class TestSemanticTraps(Loaded):
    def test_same_wording_different_meaning_is_flagged(self):
        conflicts = [c for c in rules.wording_conflicts(self.data) if c["kind"] == "same_wording_different_meaning"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["unit_ids"], ["U2", "U6"])
        self.assertEqual(set(conflicts[0]["translation_ids"]), {"T2", "T6"})

    def test_equivalence_code_reuse_across_meanings_is_flagged(self):
        conflicts = [c for c in rules.wording_conflicts(self.data) if c["kind"] == "equivalence_code_crosses_meanings"]
        codes = {c["detail"] for c in conflicts}
        self.assertIn("F2", codes)

    def test_no_false_positive_on_genuine_synonyms(self):
        # T7 and T20 are different wordings of the same meaning; not a conflict.
        conflicts = rules.wording_conflicts(self.data)
        flagged = {tid for c in conflicts for tid in c["translation_ids"]}
        self.assertNotIn("T20", flagged)


class TestRouteEvidence(Loaded):
    def test_shared_step_is_one_piece_of_evidence(self):
        shared = rules.distinct_evidence(self.data, self.routes)
        self.assertEqual(shared["T5"], 5)
        self.assertEqual(len([r for r in self.routes if "T5" in self.routes[r].route.steps]), 5)

    def test_no_route_is_releasable_today(self):
        self.assertEqual([r for r, rv in self.routes.items() if rv.releasable], [])

    def test_route_blocked_by_any_single_step(self):
        rv = self.routes["N9"]
        self.assertFalse(rv.releasable)
        self.assertEqual([s.translation_id for s in rv.steps if not s.routable], ["T7"])

    def test_editorial_routes_need_no_new_review(self):
        for rid in ("N2", "N3", "N4", "N9"):
            self.assertTrue(self.routes[rid].editorial_only, rid)
            self.assertTrue(all(r.kind == "redraft" for r in self.routes[rid].remedies))

    def test_redraft_stays_in_the_same_language(self):
        for rv in self.routes.values():
            for remedy in rv.remedies:
                if remedy.kind == "redraft":
                    source = self.data.translations[remedy.translation_id]
                    targets = [t for t in self.data.translations.values() if t.id in remedy.detail]
                    for target in targets:
                        self.assertEqual(target.language, source.language, remedy.detail)

    def test_mn_and_ru_eligibility_cannot_borrow_english_support(self):
        self.assertEqual(self.verdicts["T9"].effective_status, rules.SUPERSEDED)
        self.assertEqual(self.verdicts["T13"].effective_status, rules.SUPERSEDED)
        self.assertIn("translate", {r.kind for r in self.routes["N5"].remedies})

    def test_route_with_two_missing_reviewers_is_not_one_review_away(self):
        # T7 has 1/2 support, T4 has 1/2, but T19 has 0/2 -> N7 not one review away.
        self.assertNotIn("N7", [r for r, rv in self.routes.items() if rv.editorial_only])
        self.assertEqual(rules.routes_unlocked_by(self.data, self.verdicts, "T19"), [])

    def test_single_final_review_unlocks(self):
        self.assertEqual(rules.routes_unlocked_by(self.data, self.verdicts, "T4"), ["N1"])

    def test_corrected_unit_forces_a_version_bump(self):
        # N1 is V1; the only supported eligibility wording lives in V3 (T16).
        self.assertEqual(self.routes["N1"].version_bump, "V3")
        self.assertEqual(self.routes["N9"].version_bump, "")

    def test_alternative_wording_redraft_does_not_bump_version(self):
        # N4 stays V2 and simply swaps T7 for T20, both U7/V2.
        self.assertEqual(self.routes["N4"].version_bump, "")
        self.assertTrue(self.routes["N4"].editorial_only)


class TestCorrectionAndLegacy(Loaded):
    def test_scope_follows_the_corrected_concept(self):
        scope = rules.correction_scope(self.data)
        self.assertEqual(scope["concepts"], ["eligible"])
        self.assertEqual(scope["replacements"], ["U9"])
        self.assertEqual(scope["languages"], ["en", "mn", "ru"])

    def test_route_not_using_the_concept_is_out_of_scope(self):
        self.assertFalse(self.routes["N9"].in_correction_scope)
        self.assertFalse(self.routes["N4"].in_correction_scope)

    def test_corrupted_legacy_route_is_not_a_fallback(self):
        for rid in ("N2", "N3", "N6", "N8", "N9"):
            self.assertFalse(self.routes[rid].legacy_usable, rid)
            self.assertIn("不能作为兜底", self.routes[rid].legacy_note)

    def test_legacy_contains_the_same_defect(self):
        # N1 itself uses T2/U2, the corrected eligibility unit.
        self.assertIn("T2", self.routes["N2"].legacy_note)

    def test_bookings_have_receipts_and_need_manual_explanation(self):
        exposures = {e.route_id: e for e in rules.route_exposure(self.data, self.routes, self.verdicts)}
        self.assertEqual(exposures["N1"].bookings, ["X2"])
        self.assertEqual(exposures["N1"].needs_manual_explanation, ["X2"])
        # X2 links back to X1, the publication it relied on.
        self.assertEqual(self.data.releases["X2"].link, "X1")

    def test_every_booking_has_a_receipt(self):
        for row in self.data.releases.values():
            if row.kind == "booking":
                self.assertTrue(row.link, row.id)
                self.assertEqual(self.data.releases[row.link].kind, "published")

    def test_booking_under_corrected_wording_needs_a_manual_note(self):
        exposures = {e.route_id: e for e in rules.route_exposure(self.data, self.routes, self.verdicts)}
        # N2's X10 was booked under X4 which contains the corrected eligibility.
        self.assertEqual(exposures["N2"].needs_manual_explanation, ["X10"])
        # N2's note must not suggest the defective N1 as a fallback.
        self.assertIn("不能兜底", exposures["N2"].note)

    def test_published_but_blocked_routes_are_paused(self):
        exposures = {e.route_id: e for e in rules.route_exposure(self.data, self.routes, self.verdicts)}
        self.assertIn("暂停", exposures["N1"].note)
        self.assertEqual(exposures["N9"].published, [])


class TestWindows(Loaded):
    def test_same_person_overlapping_slots_conflict(self):
        plans = {p.window_id: p for p in rules.review_window_plan(self.data, self.verdicts)}
        self.assertEqual(set(plans["W1"].conflicts), {"W2"})
        self.assertEqual(set(plans["W5"].conflicts), {"W4"})

    def test_other_language_strength_cannot_cover_a_language_without_controls(self):
        plans = {p.window_id: p for p in rules.review_window_plan(self.data, self.verdicts)}
        self.assertFalse(plans["W5"].standing_ok)
        self.assertIn("不能用另一语言", plans["W5"].verdict)

    def test_same_unit_reader_is_not_a_second_independent_reviewer(self):
        # C already judged T7 and T20 (same unit U7); W3 must not offer them again.
        plans = {p.window_id: p for p in rules.review_window_plan(self.data, self.verdicts)}
        self.assertEqual(plans["W3"].tasks, [])

    def test_qualification_inside_slot_is_reported(self):
        plans = {p.window_id: p for p in rules.review_window_plan(self.data, self.verdicts)}
        task = plans["W2"].tasks[0]
        self.assertEqual(task.translation_id, "T4")
        self.assertIn("09:38", task.note)
        # D's own control judgements J27/J28/J31/J32 sit inside 09:00-09:45,
        # so the slot is already spent and cannot also fit a fresh review.
        self.assertIn("已用于对照判断", task.note)
        self.assertFalse(task.feasible)

    def test_qualified_reviewer_can_take_the_ru_gap(self):
        plans = {p.window_id: p for p in rules.review_window_plan(self.data, self.verdicts)}
        self.assertEqual([t.translation_id for t in plans["W6"].tasks], ["T19"])

    def test_overlapping_slots_are_resolved_not_double_booked(self):
        plans = rules.review_window_plan(self.data, self.verdicts)
        allocations = {a.reviewer: a for a in rules.allocate_windows(plans)}
        for allocation in allocations.values():
            clock = [(p.start, p.end) for p in plans if p.window_id in allocation.chosen]
            self.assertLessEqual(len(clock), 1, allocation)


class TestCoverage(Loaded):
    def test_simple_rate_and_qualified_rate_diverge(self):
        coverage = {c.concept: c for c in rules.scope_coverage(self.data, self.verdicts, "en")}
        # Five of six raw en/eligible judgements said "equivalent", yet only
        # one current translation is actually supported.
        self.assertEqual(coverage["eligible"].naive_rate, "5/6")
        self.assertEqual(coverage["eligible"].qualified_rate, "1/1")
        self.assertEqual(coverage["eligible"].stale, 3)

    def test_reasons_are_bucketed_separately(self):
        coverage = {c.concept: c for c in rules.scope_coverage(self.data, self.verdicts, "en")}
        self.assertEqual(coverage["proof"].not_independent, 2)
        self.assertEqual(coverage["proof"].missing, 0)
        self.assertEqual(coverage["pay"].unreviewed, 1)

    def test_ru_proof_missing_is_not_reviewer_gap(self):
        coverage = {c.concept: c for c in rules.scope_coverage(self.data, self.verdicts, "ru")}
        self.assertEqual(coverage["proof"].missing, 1)
        self.assertEqual(coverage["proof"].unreviewed, 1)


class TestFixtureIntegrity(unittest.TestCase):
    def test_material_concepts_are_all_high_risk(self):
        with (FIXTURES / "units.csv").open(encoding="utf-8") as source:
            for row in csv.DictReader(source):
                expected = "high" if row["concept"] in model.OUTCOME_BEARING_CONCEPTS else "low"
                self.assertEqual(row["risk"], expected, row["id"])

    def test_route_steps_are_same_language_and_not_newer_than_the_route(self):
        # A V2 route may carry unchanged steps forward from V1; what it must
        # not do is pull in a step from a *newer* version than it declares.
        data = model.load(FIXTURES)
        order = {"V1": 1, "V2": 2, "V3": 3}
        for route in data.routes.values():
            for step in route.steps:
                translation = data.translations[step]
                self.assertEqual(translation.language, route.language, route.id)
                unit = data.units[translation.unit]
                self.assertLessEqual(order[unit.version], order[route.version], f"{route.id} uses {step} from {unit.version}")

    def test_random_cross_language_wording_collision_is_tested(self):
        # Guards the premise of the "same wording" check: it is keyed by language.
        data = model.load(FIXTURES)
        same = {}
        for translation in data.translations.values():
            same.setdefault((translation.language, translation.wording), set()).add(translation.unit)
        for (language, wording), units in same.items():
            if len(units) > 1:
                self.assertEqual(language, "en")
                self.assertEqual(wording, "Six months of residence required.")


class TestReport(unittest.TestCase):
    def test_report_renders_all_sections(self):
        text = report.render(FIXTURES)
        for heading in (
            "结论先行",
            "可比覆盖",
            "实际路线",
            "语义陷阱",
            "复审时段",
            "术语办更正",
            "尚在盲审",
            "覆盖缺口",
            "逐条语义单元台账",
        ):
            self.assertIn(heading, text)

    def test_report_says_nothing_is_releasable(self):
        self.assertIn("现在就能完整开放的路线：无", report.render(FIXTURES))

    def test_report_separates_the_three_buckets(self):
        text = report.render(FIXTURES)
        self.assertIn("未独立", text)
        self.assertIn("对照不足", text)
        self.assertIn("缺内容", text)

    def test_report_flags_the_shared_wording_trap(self):
        text = report.render(FIXTURES)
        self.assertIn("Six months of residence required.", text)
        self.assertIn("不能**平移到另一含义", text)

    def test_report_does_not_recommend_cross_language_support(self):
        text = report.render(FIXTURES)
        self.assertIn("不能只靠再审", text)
        self.assertIn("不能用另一语言", text)

    def test_report_mentions_pending_review_cannot_pass(self):
        text = report.render(FIXTURES)
        self.assertIn("X7", text)
        self.assertIn("不得视为通过", text)


if __name__ == "__main__":
    unittest.main()
