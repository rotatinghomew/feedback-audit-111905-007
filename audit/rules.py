"""Audit rules for multilingual guidance release.

The rules refuse to let one number stand in for another:

* a control-pair judgement is not a judgement of a published translation;
* a control judgement with ``seen_gold=yes`` never counts toward a reviewer's
  standing, and an old translation score is never retroactively licensed by
  control practice done later;
* a reviewer's standing is evaluated **as of the moment they judged**, so only
  new judgements made after qualification count;
* high-risk wording needs two *distinct* blind reviewers;
* one person contributes at most one independent judgement per meaning, and a
  reviewer who already read a synonymous draft is not a new independent person;
* one translation shared by three routes is one piece of evidence, not three,
  and it says nothing about the steps before or after it;
* a corrected source unit makes every translation of it unusable for new
  navigation, no matter how well that translation was reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import RISK_HIGH, Dataset, Review, Route, Translation, Unit

SUPPORTED = "supported"
CONTENT_GAP = "content_gap"
DISPUTED = "disputed"
NOT_INDEPENDENT = "not_independent"
REVIEWER_GAP = "reviewer_gap"
UNVERIFIED = "unverified"
SUPERSEDED = "superseded"

STATUS_LABEL = {
    SUPPORTED: "通过（独立盲审足额）",
    CONTENT_GAP: "内容缺失/文案有误",
    DISPUTED: "存在未解分歧",
    NOT_INDEPENDENT: "未独立复核（份数不足）",
    REVIEWER_GAP: "审校员对照表现不足",
    UNVERIFIED: "未审校",
    SUPERSEDED: "旧语义稿，须按改正单元重译",
}

# Buckets used to keep "missing content", "not independently re-reviewed" and
# "reviewer cannot support this kind of call" from collapsing into one rate.
BUCKET = {
    SUPERSEDED: "stale",
    CONTENT_GAP: "missing",
    DISPUTED: "disputed",
    NOT_INDEPENDENT: "not_independent",
    REVIEWER_GAP: "reviewer_gap",
    UNVERIFIED: "unreviewed",
    SUPPORTED: "supported",
}

# Worst-first ordering used to roll per-step verdicts up to a route.
SEVERITY = {
    SUPERSEDED: 0,
    CONTENT_GAP: 1,
    DISPUTED: 2,
    NOT_INDEPENDENT: 3,
    REVIEWER_GAP: 4,
    UNVERIFIED: 5,
    SUPPORTED: 6,
}


def minutes(stamp: str) -> int:
    hour, minute = stamp.split(":")
    return int(hour) * 60 + int(minute)


# ---------------------------------------------------------------------------
# Blindness and reviewer standing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlStanding:
    reviewer: str
    language: str
    concept: str
    blind_pairs: int
    different_correct: int
    equivalent_correct: int
    wrong: int
    seen_gold: int
    usable: bool
    reason: str


def control_standing(
    dataset: Dataset,
    reviewer: str,
    language: str,
    concept: str,
    as_of: str | None = None,
) -> ControlStanding:
    """Judge a reviewer on *blind* control pairs in one language + concept.

    High risk needs both directions of the minimal pair (a ``different`` and an
    ``equivalent`` pair) answered correctly.  A reviewer who waves through every
    "equivalent" pair cannot support a high-risk call even if the pair itself is
    right, because they have not shown they can see the difference.
    ``as_of`` restricts evidence to what was already done at that moment.
    """
    cutoff = minutes(as_of) if as_of else None
    rows = [
        r
        for r in dataset.reviews.values()
        if r.control
        and r.reviewer == reviewer
        and dataset.controls[r.control].language == language
        and dataset.controls[r.control].concept == concept
        and (cutoff is None or minutes(r.at) < cutoff)
    ]
    blind = [r for r in rows if r.is_blind]
    seen = [r for r in rows if not r.is_blind]
    different_correct = equivalent_correct = wrong = 0
    for r in blind:
        gold = dataset.controls[r.control].gold
        if r.decision == gold == "different":
            different_correct += 1
        elif r.decision == gold == "equivalent":
            equivalent_correct += 1
        else:
            wrong += 1

    usable = (
        len(blind) >= 2
        and different_correct >= 1
        and equivalent_correct >= 1
        and wrong == 0
    )
    if usable:
        reason = "盲审覆盖同义与不可互换两侧且全对"
    elif not blind:
        reason = "该语言/概念没有盲审对照"
    elif wrong and different_correct == 0:
        reason = "只把同义侧判对，未识别不可互换差别（审校员理解偏差）"
    elif wrong and equivalent_correct == 0:
        reason = "只识别差别，把同义改写误判为不同"
    elif wrong:
        reason = "盲审对照存在错判"
    elif different_correct == 0 or equivalent_correct == 0:
        reason = "只覆盖对照的一侧，不足以支撑该类判断"
    else:
        reason = "盲审对照未达门槛"
    return ControlStanding(
        reviewer,
        language,
        concept,
        len(blind),
        different_correct,
        equivalent_correct,
        wrong,
        len(seen),
        usable,
        reason,
    )


def reviewer_licensed(dataset: Dataset, reviewer: str, language: str, concept: str, risk: str, as_of: str) -> bool:
    """Low-risk wording needs one recognised reviewer; high risk needs pair standing."""
    if risk != RISK_HIGH:
        return True
    return control_standing(dataset, reviewer, language, concept, as_of).usable


def qualification_time(dataset: Dataset, reviewer: str, language: str, concept: str) -> str | None:
    """The moment a reviewer's blind practice first became enough.

    Evaluated against every distinct review time in order; the first time the
    standing is usable is when qualification was earned.  Judgements made
    before it cannot borrow it retroactively.
    """
    stamps = sorted(
        {r.at for r in dataset.reviews.values() if r.reviewer == reviewer and r.is_blind},
        key=minutes,
    )
    for stamp in stamps:
        cutoff = minutes(stamp) + 1
        as_of = f"{cutoff // 60:02d}:{cutoff % 60:02d}"
        if control_standing(dataset, reviewer, language, concept, as_of).usable:
            return stamp
    return None


@dataclass(frozen=True)
class ReviewUse:
    review: Review
    usable: bool
    state: str
    reason: str


def review_use(dataset: Dataset, review: Review) -> ReviewUse:
    """Classify one judgement record.  Only ``usable`` rows count as evidence."""
    if not review.translation:
        return ReviewUse(review, False, "control", "对照记录，不是对正式文案的判断")
    if not review.is_blind:
        return ReviewUse(review, False, "seen_gold", "看过标准解释，不算盲审")
    translation = dataset.translations[review.translation]
    unit = dataset.units[translation.unit]
    if not reviewer_licensed(dataset, review.reviewer, translation.language, unit.concept, unit.risk, review.at):
        return ReviewUse(
            review,
            False,
            "no_standing",
            f"{review.reviewer} 在判断时点尚无 {translation.language}/{unit.concept} 的合格独立对照表现",
        )
    return ReviewUse(review, True, "usable", "独立盲审")


# ---------------------------------------------------------------------------
# Correction scope
# ---------------------------------------------------------------------------


def superseded_index(dataset: Dataset) -> dict[str, str]:
    """old unit id -> replacement unit id, from the term office's correction rows."""
    out: dict[str, str] = {}
    for unit in dataset.units.values():
        if unit.is_correction and unit.replacement:
            out[unit.correction] = unit.replacement
    return out


def correction_scope(dataset: Dataset) -> dict[str, object]:
    """Units, concepts and languages touched by a source correction.

    A correction propagates through every translation of the faulty unit, in
    every language.  Routes that never use the concept are out of scope.
    """
    scope = superseded_index(dataset)
    concepts: set[str] = set()
    languages: set[str] = set()
    replacements: set[str] = set()
    for old, new in scope.items():
        replace = dataset.units[new]
        concepts.add(replace.concept)
        replacements.add(new)
        for t in dataset.translations_of(old):
            languages.add(t.language)
        for t in dataset.translations_of(new):
            languages.add(t.language)
    return {
        "units": sorted(scope),
        "replacements": sorted(replacements),
        "concepts": sorted(concepts),
        "languages": sorted(languages),
    }


# ---------------------------------------------------------------------------
# Unit / translation verdicts
# ---------------------------------------------------------------------------


@dataclass
class UnitVerdict:
    translation: Translation
    unit: Unit
    evidence_status: str
    support: list[Review] = field(default_factory=list)
    oppose: list[Review] = field(default_factory=list)
    seen_gold: list[Review] = field(default_factory=list)
    no_standing: list[Review] = field(default_factory=list)
    superseded_by: str = ""
    note: str = ""

    @property
    def risk(self) -> str:
        return self.unit.risk

    @property
    def quota(self) -> int:
        return 2 if self.unit.risk == RISK_HIGH else 1

    @property
    def effective_status(self) -> str:
        return SUPERSEDED if self.superseded_by else self.evidence_status

    @property
    def usable_for_new_navigation(self) -> bool:
        return self.effective_status == SUPPORTED

    @property
    def distinct_support(self) -> int:
        return len({r.reviewer for r in self.support})

    @property
    def bucket(self) -> str:
        return BUCKET[self.effective_status]

    @property
    def summary(self) -> str:
        bits = [STATUS_LABEL[self.effective_status]]
        if self.superseded_by:
            bits.append(f"改正为 {self.superseded_by}")
        if self.effective_status != SUPERSEDED:
            bits.append(f"独立认可 {self.distinct_support}（门槛 {self.quota}）")
        return "；".join(bits)


def unit_verdict(dataset: Dataset, translation_id: str) -> UnitVerdict:
    translation = dataset.translations[translation_id]
    unit = dataset.units[translation.unit]
    verdict = UnitVerdict(
        translation=translation,
        unit=unit,
        evidence_status=UNVERIFIED,
        superseded_by=superseded_index(dataset).get(unit.id, ""),
    )

    # One person may read several drafts of the same text; that is still one
    # independent judgement, so group by reviewer.
    valid_by_reviewer: dict[str, list[Review]] = {}
    for review in dataset.reviews_by_translation.get(translation_id, []):
        use = review_use(dataset, review)
        if use.state == "seen_gold":
            verdict.seen_gold.append(review)
        elif use.state == "no_standing":
            verdict.no_standing.append(review)
        elif use.usable:
            valid_by_reviewer.setdefault(review.reviewer, []).append(review)

    for rows in valid_by_reviewer.values():
        if any(r.decision == "different" for r in rows):
            verdict.oppose.extend(rows)
        else:
            verdict.support.extend(rows)

    if verdict.support and verdict.oppose:
        verdict.evidence_status = DISPUTED
        verdict.note = "合格审校之间正反并存：需第三份独立判断或改稿"
    elif verdict.oppose:
        verdict.evidence_status = CONTENT_GAP
        verdict.note = "合格审校认为含义改变，且无人以合格证据认可"
    elif verdict.distinct_support >= verdict.quota:
        verdict.evidence_status = SUPPORTED
        verdict.note = f"{verdict.distinct_support} 名独立审校认可，达到 {verdict.quota} 名门槛"
    elif verdict.support:
        verdict.evidence_status = NOT_INDEPENDENT
        verdict.note = f"仅 {verdict.distinct_support} 名独立审校认可，高风险需 {verdict.quota} 名"
    elif verdict.no_standing:
        verdict.evidence_status = REVIEWER_GAP
        verdict.note = "现有判断来自对照表现不足的审校，需另找合格审校"
    elif verdict.seen_gold:
        verdict.evidence_status = REVIEWER_GAP
        verdict.note = "现有判断都看过标准解释，需未见稿的盲审"
    else:
        verdict.evidence_status = UNVERIFIED
        verdict.note = "无任何判断记录"
    return verdict


def all_unit_verdicts(dataset: Dataset) -> dict[str, UnitVerdict]:
    return {tid: unit_verdict(dataset, tid) for tid in dataset.translations}


def alternative_wording(dataset: Dataset, verdicts: dict[str, UnitVerdict], translation: Translation) -> list[str]:
    """Other translations of the same unit + language already supported."""
    return sorted(
        t.id
        for t in dataset.translations.values()
        if t.id != translation.id
        and t.unit == translation.unit
        and t.language == translation.language
        and verdicts[t.id].usable_for_new_navigation
    )


def replacement_wording(dataset: Dataset, verdicts: dict[str, UnitVerdict], unit: Unit, language: str) -> list[str]:
    """Supported translations of the corrected unit, in the same language."""
    replacement = superseded_index(dataset).get(unit.id)
    if not replacement:
        return []
    return sorted(
        t.id
        for t in dataset.translations_of(replacement)
        if t.language == language and verdicts[t.id].usable_for_new_navigation
    )


def replacement_drafts(dataset: Dataset, unit: Unit, language: str) -> list[str]:
    replacement = superseded_index(dataset).get(unit.id)
    if not replacement:
        return []
    return sorted(t.id for t in dataset.translations_of(replacement) if t.language == language)


# ---------------------------------------------------------------------------
# Wording / equivalence traps
# ---------------------------------------------------------------------------


def wording_conflicts(dataset: Dataset) -> list[dict[str, object]]:
    """Same wording (or same equivalence code) bound to *materially different* units.

    Two source versions may reuse one sentence with different force.  That is a
    trap only when the meanings differ; re-versioning the same meaning is fine.
    """
    conflicts = []
    by_wording: dict[tuple[str, str], list[Translation]] = {}
    for t in dataset.translations.values():
        by_wording.setdefault((t.language, t.wording), []).append(t)
    for (language, wording), rows in sorted(by_wording.items()):
        meanings = {dataset.units[r.unit].meaning for r in rows}
        if len(meanings) > 1:
            conflicts.append(
                {
                    "kind": "same_wording_different_meaning",
                    "language": language,
                    "detail": wording,
                    "translation_ids": sorted(r.id for r in rows),
                    "unit_ids": sorted({r.unit for r in rows}),
                    "meanings": sorted(meanings),
                }
            )
    by_code: dict[str, list[Translation]] = {}
    for t in dataset.translations.values():
        by_code.setdefault(t.equivalence, []).append(t)
    for code, rows in sorted(by_code.items()):
        meanings = {dataset.units[r.unit].meaning for r in rows}
        if len(meanings) > 1:
            conflicts.append(
                {
                    "kind": "equivalence_code_crosses_meanings",
                    "language": "/".join(sorted({r.language for r in rows})),
                    "detail": code,
                    "translation_ids": sorted(r.id for r in rows),
                    "unit_ids": sorted({r.unit for r in rows}),
                    "meanings": sorted(meanings),
                }
            )
    return conflicts


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@dataclass
class StepVerdict:
    translation_id: str
    verdict: UnitVerdict
    shared_with: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.verdict.effective_status

    @property
    def routable(self) -> bool:
        return self.verdict.usable_for_new_navigation


@dataclass
class Remedy:
    translation_id: str
    kind: str  # review | review_many | rewrite | redraft | translate | translate_then_review
    detail: str
    version_bump: str = ""  # target unit's version if newer than the route's


@dataclass
class RouteVerdict:
    route: Route
    steps: list[StepVerdict]
    status: str
    releasable: bool
    in_correction_scope: bool
    legacy_usable: bool
    legacy_note: str
    remedies: list[Remedy] = field(default_factory=list)

    @property
    def shared_evidence(self) -> list[str]:
        return sorted({s.translation_id for s in self.steps if s.shared_with})

    @property
    def editorial_only(self) -> bool:
        """Every blocker is a redraft to an already-supported wording."""
        return bool(self.remedies) and all(r.kind == "redraft" for r in self.remedies)

    @property
    def version_bump(self) -> str:
        bumps = sorted({r.version_bump for r in self.remedies if r.version_bump})
        return bumps[-1] if bumps else ""

    @property
    def blocking(self) -> list[str]:
        return [
            f"{s.translation_id}/{s.verdict.unit.id}: {STATUS_LABEL[s.status]}"
            for s in self.steps
            if not s.routable
        ]

    @property
    def dependency_complete(self) -> bool:
        """Every step has *some* judgement on record, so the evidence chain is closed.

        This is what an editor may sign off on alone: nothing is silently
        missing from the dependency graph.  It says nothing about whether the
        semantic unit is actually understood -- a route can be dependency
        complete and still fail on a reviewer gap or a content defect.
        """
        return all(s.status != UNVERIFIED for s in self.steps)

    @property
    def pending_review(self) -> bool:
        """Some step still lacks the judgement it is waiting for."""
        return any(s.status == UNVERIFIED and not s.verdict.superseded_by for s in self.steps)


def _routes_sharing(dataset: Dataset, translation_id: str) -> list[str]:
    return sorted(r.id for r in dataset.routes.values() if translation_id in r.steps)


def _bump(route: Route, unit: Unit) -> str:
    order = {"V1": 1, "V2": 2, "V3": 3}
    return unit.version if order.get(unit.version, 0) > order.get(route.version, 0) else ""


def _remedies(
    dataset: Dataset,
    verdicts: dict[str, UnitVerdict],
    steps: list[StepVerdict],
    route: Route,
) -> list[Remedy]:
    out: list[Remedy] = []
    for step in steps:
        verdict = step.verdict
        if verdict.usable_for_new_navigation:
            continue
        tid = step.translation_id
        language = verdict.translation.language
        status = verdict.effective_status
        if status == SUPERSEDED:
            replacements = replacement_wording(dataset, verdicts, verdict.unit, language)
            drafts = replacement_drafts(dataset, verdict.unit, language)
            bump = _bump(route, dataset.units[verdict.superseded_by])
            if replacements:
                out.append(
                    Remedy(
                        tid,
                        "redraft",
                        f"改用改正单元 {verdict.superseded_by} 的已审译文 {', '.join(replacements)}",
                        bump,
                    )
                )
            elif drafts:
                out.append(
                    Remedy(
                        tid,
                        "translate_then_review",
                        f"改正单元 {verdict.superseded_by} 的 {language} 草稿 {', '.join(drafts)} 未达门槛，需先审校",
                        bump,
                    )
                )
            else:
                out.append(
                    Remedy(
                        tid,
                        "translate",
                        f"改正单元 {verdict.superseded_by} 尚无 {language} 译稿",
                        bump,
                    )
                )
        elif status in (CONTENT_GAP, DISPUTED):
            out.append(Remedy(tid, "rewrite", verdict.note))
        elif status == NOT_INDEPENDENT:
            alternatives = alternative_wording(dataset, verdicts, verdict.translation)
            if alternatives:
                target = dataset.units[dataset.translations[alternatives[0]].unit]
                out.append(
                    Remedy(tid, "redraft", f"同单元已有足额认可的另一种说法 {', '.join(alternatives)}", _bump(route, target))
                )
            else:
                need = verdict.quota - verdict.distinct_support
                kind = "review" if need == 1 else "review_many"
                out.append(Remedy(tid, kind, f"再需 {need} 名未看过本稿或同义稿的合格独立审校"))
        else:
            alternatives = alternative_wording(dataset, verdicts, verdict.translation)
            if alternatives:
                target = dataset.units[dataset.translations[alternatives[0]].unit]
                out.append(
                    Remedy(tid, "redraft", f"同单元已有足额认可的另一种说法 {', '.join(alternatives)}", _bump(route, target))
                )
            else:
                need = verdict.quota - verdict.distinct_support
                kind = "review" if need == 1 else "review_many"
                out.append(Remedy(tid, kind, f"需从头取得 {need} 名合格独立盲审"))
    return out


def route_verdict(dataset: Dataset, verdicts: dict[str, UnitVerdict], route: Route) -> RouteVerdict:
    steps = [StepVerdict(tid, verdicts[tid], [r for r in _routes_sharing(dataset, tid) if r != route.id]) for tid in route.steps]
    worst = min(steps, key=lambda s: SEVERITY[s.status]).status
    releasable = all(s.routable for s in steps)
    scope = correction_scope(dataset)
    in_scope = any(verdicts[s.translation_id].unit.id in scope["units"] for s in steps)

    legacy_usable = False
    legacy_note = "无人工留存路径"
    if route.legacy:
        legacy_usable, legacy_note = _legacy_assessment(dataset, verdicts, route, steps)

    return RouteVerdict(
        route=route,
        steps=steps,
        status=worst,
        releasable=releasable,
        in_correction_scope=in_scope,
        legacy_usable=legacy_usable,
        legacy_note=legacy_note,
        remedies=_remedies(dataset, verdicts, steps, route),
    )


def _legacy_assessment(
    dataset: Dataset,
    verdicts: dict[str, UnitVerdict],
    route: Route,
    steps: list[StepVerdict],
) -> tuple[bool, str]:
    """Can the retained old route cover the concepts this route is blocked on?

    A fallback is only useful for the *blocked* concepts.  It is disqualified if
    the old route itself carries the same defect or the corrected source unit,
    or if it never contained that concept at all.
    """
    legacy = dataset.routes[route.legacy]
    blocked_concepts = {s.verdict.unit.concept for s in steps if not s.routable}
    if not blocked_concepts:
        return False, f"本路线无需兜底；旧路径 {legacy.id} 保留备查"

    defects = []
    missing = []
    covered = []
    for concept in sorted(blocked_concepts):
        relevant = [
            verdicts[t]
            for t in legacy.steps
            if verdicts[t].unit.concept == concept
        ]
        if not relevant:
            missing.append(concept)
            continue
        bad = [
            v
            for v in relevant
            if v.effective_status in (SUPERSEDED, CONTENT_GAP, DISPUTED)
            or not v.usable_for_new_navigation
        ]
        if bad:
            defects.append(
                f"{concept}（"
                + "、".join(f"{v.translation.id}/{v.unit.id}: {STATUS_LABEL[v.effective_status]}" for v in bad)
                + "）"
            )
        else:
            covered.append(concept)

    if defects:
        return False, f"旧路径 {legacy.id} 在阻塞概念上同样不可用：{'；'.join(defects)}，不能作为兜底"
    if missing:
        return False, f"旧路径 {legacy.id} 缺少阻塞概念 {'、'.join(missing)}，无法作为对应步骤的兜底"
    return True, f"旧路径 {legacy.id} 在 {'、'.join(covered)} 上已达门槛，可作为含义相容说明的底稿"


def all_route_verdicts(dataset: Dataset, verdicts: dict[str, UnitVerdict]) -> dict[str, RouteVerdict]:
    return {rid: route_verdict(dataset, verdicts, route) for rid, route in dataset.routes.items()}


def routes_unlocked_by(dataset: Dataset, verdicts: dict[str, UnitVerdict], translation_id: str) -> list[str]:
    """Routes that open if *one more* independent review of this draft lands.

    Only meaningful when this is the last reviewer the unit needs.  A high-risk
    unit at 0/2 is not unlocked by a single new judgement.  A ``redraft`` is an
    editorial action using already-supported wording, so it does not consume
    review capacity; rewrites and missing translations exclude the route.
    """
    verdict = verdicts[translation_id]
    if verdict.distinct_support + 1 < verdict.quota:
        return []
    out = []
    for rid, route in dataset.routes.items():
        rv = route_verdict(dataset, verdicts, route)
        if rv.releasable:
            continue
        if any(r.kind not in ("review", "redraft") for r in rv.remedies):
            continue
        review_blockers = [r.translation_id for r in rv.remedies if r.kind == "review"]
        if review_blockers == [translation_id]:
            out.append(rid)
    return out


# ---------------------------------------------------------------------------
# Comparable coverage per language, concept, risk and route
# ---------------------------------------------------------------------------


@dataclass
class ScopeCoverage:
    language: str
    concept: str
    risk: str
    naive_reviews: int
    naive_equivalent: int
    material: int
    supported: int
    disputed: int
    missing: int
    not_independent: int
    reviewer_gap: int
    unreviewed: int
    stale: int

    @property
    def naive_rate(self) -> str:
        if not self.naive_reviews:
            return "—"
        return f"{self.naive_equivalent}/{self.naive_reviews}"

    @property
    def qualified_rate(self) -> str:
        if not self.material:
            return "—"
        return f"{self.supported}/{self.material}"

    @property
    def comparable(self) -> str:
        return (
            f"{self.qualified_rate} 通过；缺内容 {self.missing}，有分歧 {self.disputed}，"
            f"未独立 {self.not_independent}，对照不足 {self.reviewer_gap}，未审 {self.unreviewed}"
        )


def scope_coverage(dataset: Dataset, verdicts: dict[str, UnitVerdict], language: str) -> list[ScopeCoverage]:
    out = []
    for concept in sorted({u.concept for u in dataset.units.values()}):
        tids = [
            t.id
            for t in dataset.translations.values()
            if t.language == language and dataset.units[t.unit].concept == concept
        ]
        if not tids:
            continue
        risk = RISK_HIGH if any(dataset.units[dataset.translations[t].unit].risk == RISK_HIGH for t in tids) else "low"
        reviews = [r for tid in tids for r in dataset.reviews_by_translation.get(tid, [])]
        counts = {k: 0 for k in BUCKET.values()}
        for tid in tids:
            counts[verdicts[tid].bucket] += 1
        material = counts["supported"] + counts["missing"] + counts["disputed"] + counts["not_independent"] + counts["reviewer_gap"] + counts["unreviewed"]
        out.append(
            ScopeCoverage(
                language=language,
                concept=concept,
                risk=risk,
                naive_reviews=len(reviews),
                naive_equivalent=sum(1 for r in reviews if r.decision == "equivalent"),
                material=material,
                supported=counts["supported"],
                disputed=counts["disputed"],
                missing=counts["missing"],
                not_independent=counts["not_independent"],
                reviewer_gap=counts["reviewer_gap"],
                unreviewed=counts["unreviewed"],
                stale=counts["stale"],
            )
        )
    return out


@dataclass
class RouteCoverage:
    route_id: str
    language: str
    version: str
    desk: str
    step_count: int
    supported_steps: int
    blocking_steps: list[str]
    shared_steps: list[str]
    status: str
    releasable: bool
    dependency_complete: bool
    pending_review: bool


def route_coverage(dataset: Dataset, route_verdicts: dict[str, RouteVerdict]) -> list[RouteCoverage]:
    out = []
    for rid, rv in route_verdicts.items():
        out.append(
            RouteCoverage(
                route_id=rid,
                language=rv.route.language,
                version=rv.route.version,
                desk=rv.route.desk,
                step_count=len(rv.steps),
                supported_steps=sum(1 for s in rv.steps if s.routable),
                blocking_steps=rv.blocking,
                shared_steps=rv.shared_evidence,
                status=rv.status,
                releasable=rv.releasable,
                dependency_complete=rv.dependency_complete,
                pending_review=rv.pending_review,
            )
        )
    return out


def distinct_evidence(dataset: Dataset, route_verdicts: dict[str, RouteVerdict]) -> dict[str, int]:
    """How much evidence each route contributes vs. reuses.

    A step shared by several routes is one piece of evidence; counting it per
    route would inflate the apparent coverage.
    """
    seen: dict[str, int] = {}
    for rv in route_verdicts.values():
        for step in rv.steps:
            seen.setdefault(step.translation_id, 0)
            seen[step.translation_id] += 1
    return seen


# ---------------------------------------------------------------------------
# Review windows
# ---------------------------------------------------------------------------


@dataclass
class WindowTask:
    translation_id: str
    unit_id: str
    concept: str
    risk: str
    would_unlock: list[str]
    used_by: list[str]
    reason: str
    partial: bool
    feasible_from: str
    feasible: bool
    note: str = ""


@dataclass
class WindowPlan:
    window_id: str
    reviewer: str
    language: str
    concept: str
    start: str
    end: str
    minutes: int
    standing: ControlStanding
    standing_ok: bool
    tasks: list[WindowTask]
    conflicts: list[str]
    verdict: str


def _already_read(dataset: Dataset, reviewer: str, unit_id: str) -> bool:
    """A reviewer who read this unit, in any wording, is not a fresh reader."""
    return any(
        r.reviewer == reviewer
        and r.translation
        and dataset.translations[r.translation].unit == unit_id
        for r in dataset.reviews.values()
    )


def review_window_plan(dataset: Dataset, verdicts: dict[str, UnitVerdict]) -> list[WindowPlan]:
    windows = list(dataset.windows.values())
    plans = []
    for window in windows:
        conflicts = [
            w.id
            for w in windows
            if w.id != window.id
            and w.reviewer == window.reviewer
            and minutes(w.start) < minutes(window.end)
            and minutes(window.start) < minutes(w.end)
        ]
        standing = control_standing(dataset, window.reviewer, window.language, window.concept, window.end)
        high_risk_here = any(
            dataset.units[t.unit].risk == RISK_HIGH
            and t.language == window.language
            and dataset.units[t.unit].concept == window.concept
            for t in dataset.translations.values()
        )
        standing_ok = standing.usable or not high_risk_here

        tasks: list[WindowTask] = []
        for t in dataset.translations.values():
            if t.language != window.language:
                continue
            unit = dataset.units[t.unit]
            if unit.concept != window.concept:
                continue
            verdict = verdicts[t.id]
            if verdict.usable_for_new_navigation or verdict.superseded_by:
                continue
            if _already_read(dataset, window.reviewer, unit.id):
                continue  # same person, same or synonymous draft: not a second independent call
            if unit.risk == RISK_HIGH and not standing.usable:
                continue
            unlocked = routes_unlocked_by(dataset, verdicts, t.id)
            used_by = sorted(rid for rid, rv in all_route_verdicts(dataset, verdicts).items() if t.id in rv.route.steps)
            # A high-risk judgement can only start once the reviewer has earned
            # pair standing.  If that happens inside the slot (their control
            # pairs are logged during it), the remaining minutes may not fit a
            # full review.  A slot shorter than the task is never feasible.
            qualifies_at = (
                qualification_time(dataset, window.reviewer, window.language, window.concept)
                if unit.risk == RISK_HIGH
                else None
            )
            # The reviewer is only free once qualified.  If they qualified inside
            # the slot, the tail may be too short for a fresh independent read,
            # and any judgement they logged inside the slot shows they were busy.
            busy = [
                r.id
                for r in dataset.reviews.values()
                if r.reviewer == window.reviewer
                and minutes(window.start) <= minutes(r.at) < minutes(window.end)
            ]
            start_at = max(minutes(window.start), minutes(qualifies_at) if qualifies_at else minutes(window.start))
            remaining = minutes(window.end) - start_at
            feasible = remaining > 0 and not busy
            note_bits = []
            if qualifies_at and minutes(qualifies_at) > minutes(window.start):
                note_bits.append(f"{qualifies_at} 才取得本范围资格，此后仅剩 {max(remaining, 0)} 分钟")
            if busy:
                note_bits.append("该时段已用于对照判断（" + "、".join(busy) + "）")
            tasks.append(
                WindowTask(
                    t.id,
                    unit.id,
                    unit.concept,
                    unit.risk,
                    unlocked,
                    used_by,
                    verdict.note,
                    partial=not unlocked,
                    feasible_from=qualifies_at or window.start,
                    feasible=feasible,
                    note="；".join(note_bits),
                )
            )
        tasks.sort(key=lambda x: (not x.feasible, x.risk != RISK_HIGH, x.partial, x.translation_id))

        feasible_tasks = [t for t in tasks if t.feasible]
        if not standing_ok:
            verdict = f"不可用：{standing.reason}；不能用另一语言较好的审校覆盖本语言关键语义"
        elif not feasible_tasks:
            if tasks:
                verdict = "本时段做不完：" + "；".join(f"{t.translation_id} {t.note}" for t in tasks)
            else:
                verdict = "无独立可做项，可释放该时段"
        else:
            verdict = f"可补做 {len(feasible_tasks)} 项：" + "、".join(t.translation_id for t in feasible_tasks)
        plans.append(
            WindowPlan(
                window_id=window.id,
                reviewer=window.reviewer,
                language=window.language,
                concept=window.concept,
                start=window.start,
                end=window.end,
                minutes=window.minutes,
                standing=standing,
                standing_ok=standing_ok,
                tasks=tasks,
                conflicts=conflicts,
                verdict=verdict,
            )
        )
    return plans


@dataclass
class Allocation:
    reviewer: str
    chosen: list[str]
    released: list[str]
    reason: str


def _overlap(a: WindowPlan, b: WindowPlan) -> bool:
    return minutes(a.start) < minutes(b.end) and minutes(b.start) < minutes(a.end)


def _clusters(group: list[WindowPlan]) -> list[list[WindowPlan]]:
    remaining = sorted(group, key=lambda p: p.window_id)
    clusters: list[list[WindowPlan]] = []
    for plan in remaining:
        placed = False
        for cluster in clusters:
            if any(_overlap(plan, other) for other in cluster):
                cluster.append(plan)
                placed = True
                break
        if not placed:
            clusters.append([plan])
    return clusters


def allocate_windows(plans: list[WindowPlan]) -> list[Allocation]:
    """Resolve overlapping reviewer slots; keep every non-conflicting slot.

    A reviewer can only be in one place at a time.  Within each overlapping
    cluster prefer the slot that unlocks the most routes, then one that makes
    any progress, then the one with more tasks.  Slots at other times stay.
    """
    out: list[Allocation] = []
    by_reviewer: dict[str, list[WindowPlan]] = {}
    for plan in plans:
        by_reviewer.setdefault(plan.reviewer, []).append(plan)
    for reviewer, group in sorted(by_reviewer.items()):
        chosen: list[str] = []
        released: list[str] = []
        reasons: list[str] = []
        for cluster in _clusters(group):
            usable = [p for p in cluster if p.standing_ok and any(t.feasible for t in p.tasks)]
            if not usable:
                released.extend(p.window_id for p in cluster)
                for p in cluster:
                    reasons.append(f"{p.window_id} 释放（{p.verdict}）")
                continue
            pick = max(
                usable,
                key=lambda p: (
                    sum(len(t.would_unlock) for t in p.tasks if t.feasible),
                    sum(1 for t in p.tasks if t.feasible and not t.partial),
                    sum(1 for t in p.tasks if t.feasible),
                ),
            )
            chosen.append(pick.window_id)
            released.extend(p.window_id for p in cluster if p.window_id != pick.window_id)
            unlocked = sorted({r for t in pick.tasks if t.feasible for r in t.would_unlock})
            reasons.append(
                f"{pick.window_id}（{pick.language}/{pick.concept}）"
                + (f" 可解锁 {', '.join(unlocked)}" if unlocked else " 只推进证据，不单靠本项解锁路线")
            )
        out.append(Allocation(reviewer, chosen, released, "；".join(reasons) or "无可用时段"))
    return out


# ---------------------------------------------------------------------------
# Publications, receipts and appointments
# ---------------------------------------------------------------------------


@dataclass
class RouteExposure:
    route_id: str
    desk: str
    language: str
    version: str
    published: list[str]
    bookings: list[str]
    requested: bool
    in_correction_scope: bool
    releasable: bool
    needs_manual_explanation: list[str]
    paused_steps: list[str]
    note: str


def route_exposure(
    dataset: Dataset,
    route_verdicts: dict[str, RouteVerdict],
    verdicts: dict[str, UnitVerdict],
) -> list[RouteExposure]:
    out = []
    corrected_units = set(superseded_index(dataset))
    for rid, rv in route_verdicts.items():
        released = [r for r in dataset.releases.values() if r.route == rid]
        published = sorted(r.id for r in released if r.kind == "published")
        bookings = sorted(r.id for r in released if r.kind == "booking")
        requested = any(r.kind == "requested" for r in released)
        # A booking taken under a version whose eligibility wording was later
        # corrected keeps the original promise; the desk needs a manual note.
        needs_manual = [
            booking
            for booking in bookings
            if any(verdicts[s.translation_id].unit.id in corrected_units for s in rv.steps)
        ]
        paused = [s.translation_id for s in rv.steps if s.verdict.superseded_by or not s.routable]
        if rv.releasable:
            note = "可按现行证据开放"
        else:
            status = "已请求开放" if requested else ("已上线" if published else "尚未上线")
            if rv.in_correction_scope and any(s.verdict.superseded_by for s in rv.steps):
                note = f"{status}；含被更正的资格条件，该步骤暂停，改发改正后语义；旧文本不得作为新发布依据"
            elif published:
                note = f"{status}；其他步骤可继续，阻塞步骤须暂停，不得前段新版、后段旧版拼接"
            elif requested:
                note = f"{status}；须与新证据一并重查后再发"
            else:
                note = f"{status}；补齐阻塞语义后再排发布"
        if rv.route.legacy and published:
            fallback = (
                f"旧版 {rv.route.legacy} 可在阻塞概念上作含义相容说明"
                if rv.legacy_usable
                else f"旧版 {rv.route.legacy} 不能兜底（含同一缺陷或缺少相应概念）"
            )
            note += f"；继续办理的旧预约按登记的旧版承诺处理；{fallback}"
        out.append(
            RouteExposure(
                route_id=rid,
                desk=rv.route.desk,
                language=rv.route.language,
                version=rv.route.version,
                published=published,
                bookings=bookings,
                requested=requested,
                in_correction_scope=rv.in_correction_scope,
                releasable=rv.releasable,
                needs_manual_explanation=sorted(needs_manual),
                paused_steps=paused,
                note=note,
            )
        )
    return out


def in_flight(dataset: Dataset) -> list[str]:
    return sorted(r.id for r in dataset.releases.values() if r.kind == "review_pending")


def concept_gaps(dataset: Dataset, verdicts: dict[str, UnitVerdict]) -> list[str]:
    """Outcome-bearing concepts with no supported translation, or no route at all."""
    out = []
    routed = {t for r in dataset.routes.values() for t in r.steps}
    for concept in sorted({u.concept for u in dataset.units.values()}):
        if not any(u.risk == RISK_HIGH for u in dataset.units.values() if u.concept == concept):
            continue
        units = [u for u in dataset.units.values() if u.concept == concept]
        if not any(t in routed for u in units for t in {x.id for x in dataset.translations_of(u.id)}):
            out.append(f"语义概念 {concept} 的所有译稿都未出现在任何实际路线，无法用路线证据说明已读懂")
            continue
        for language in sorted({t.language for u in units for t in dataset.translations_of(u.id)}):
            tids = [t.id for u in units for t in dataset.translations_of(u.id) if t.language == language]
            if not any(verdicts[t].usable_for_new_navigation for t in tids):
                out.append(f"{language}/{concept} 没有任何达到门槛的现行译文")
    return out


# ---------------------------------------------------------------------------
# Disagreement triage
# ---------------------------------------------------------------------------


@dataclass
class Disagreement:
    translation_id: str
    unit_id: str
    supporters: list[str]
    opposers: list[str]
    excluded: list[str]
    excluded_view: str
    reading: str


def _bias(dataset: Dataset, reviewer: str, language: str, concept: str) -> str:
    """A repeatable misreading visible in the reviewer's own blind control record."""
    st = control_standing(dataset, reviewer, language, concept)
    if st.wrong and st.different_correct == 0:
        return "只认同义侧、看不出不可互换差别"
    if st.wrong and st.equivalent_correct == 0:
        return "把同义改写误判为不同"
    return ""


def triage_disagreements(dataset: Dataset, verdicts: dict[str, UnitVerdict]) -> list[Disagreement]:
    """Separate a real copy defect from one kind of reviewer misreading it.

    The test is the reviewer's *own* blind control record.  If the people who
    accept the draft are the ones who cannot see the distinction the draft
    turns on, their acceptance is a comprehension bias, not evidence.  If the
    people who reject it show that same bias, the rejection is weak.  Judgements
    that saw the gold standard or lacked pair standing are listed separately
    and never count as a second opinion.
    """
    out: list[Disagreement] = []
    for tid in sorted(verdicts):
        verdict = verdicts[tid]
        if not verdict.oppose:
            continue
        language = verdict.translation.language
        concept = verdict.unit.concept
        supporters = sorted({r.reviewer for r in verdict.support})
        opposers = sorted({r.reviewer for r in verdict.oppose})
        excluded_rows = verdict.no_standing + verdict.seen_gold
        excluded = sorted({r.reviewer for r in excluded_rows})
        excluded_view = ""
        if verdict.seen_gold:
            excluded_view += "有判断已看过标准解释（" + "、".join(r.id for r in verdict.seen_gold) + "）"
        if verdict.no_standing:
            excluded_view += ("；" if excluded_view else "") + "有判断来自判断时点尚无对照资格的人（" + "、".join(r.id for r in verdict.no_standing) + "）"

        supporter_bias = {r: _bias(dataset, r, language, concept) for r in supporters}
        opposer_bias = {r: _bias(dataset, r, language, concept) for r in opposers}
        bad_support = {r: b for r, b in supporter_bias.items() if b}
        bad_oppose = {r: b for r, b in opposer_bias.items() if b}

        if bad_support and not bad_oppose:
            reading = (
                "认可方向只来自理解偏差者（"
                + "；".join(f"{r} {b}" for r, b in sorted(bad_support.items()))
                + "），不能抵消合格反对意见：按文案遗漏关键限定处理，须改稿"
            )
        elif bad_oppose and not bad_support:
            reading = (
                "反对方向只来自理解偏差者（"
                + "；".join(f"{r} {b}" for r, b in sorted(bad_oppose.items()))
                + "），现稿可保留，但需一份无该偏差的独立确认"
            )
        elif supporters:
            reading = "双方对照表现均可支撑各自判断，属真正的文案分歧：需第三份独立盲审或改稿"
        else:
            reading = "反对意见来自合格审校，且无任何合格证据支持现稿：按内容缺失处理，须改稿或补译"
        out.append(Disagreement(tid, verdict.unit.id, supporters, opposers, excluded, excluded_view, reading))
    return out
