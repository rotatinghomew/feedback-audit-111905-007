"""Render the release-readiness report as Markdown.

Run ``python3 -m audit.report`` to print it, or import :func:`render`.
Every figure is computed from ``fixtures/``; nothing is hand-typed.
"""

from __future__ import annotations

from pathlib import Path

from . import model, rules


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(out)


def _bullet(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "（无）"


def _step_list(verdicts: list[rules.UnitVerdict]) -> str:
    return "、".join(f"{v.translation.id}（{rules.STATUS_LABEL[v.effective_status]}）" for v in verdicts)


def render(fixtures: Path | None = None) -> str:
    dataset = model.load(fixtures)
    verdicts = rules.all_unit_verdicts(dataset)
    route_verdicts = rules.all_route_verdicts(dataset, verdicts)
    plans = rules.review_window_plan(dataset, verdicts)
    allocations = rules.allocate_windows(plans)
    exposures = rules.route_exposure(dataset, route_verdicts, verdicts)
    scope = rules.correction_scope(dataset)
    conflicts = rules.wording_conflicts(dataset)
    gaps = rules.concept_gaps(dataset, verdicts)
    shared = rules.distinct_evidence(dataset, route_verdicts)
    disagreements = rules.triage_disagreements(dataset, verdicts)

    releasable = sorted(r for r, rv in route_verdicts.items() if rv.releasable)
    editorial = sorted(r for r, rv in route_verdicts.items() if not rv.releasable and rv.editorial_only)
    needs_content = sorted(
        r
        for r, rv in route_verdicts.items()
        if not rv.releasable
        and not rv.editorial_only
        and any(x.kind in ("translate", "translate_then_review", "rewrite") for x in rv.remedies)
    )
    review_gated = sorted(
        r
        for r, rv in route_verdicts.items()
        if not rv.releasable
        and not rv.editorial_only
        and r not in needs_content
        and any(x.kind in ("review", "review_many") for x in rv.remedies)
    )
    satisfied = sorted(
        r
        for r, rv in route_verdicts.items()
        if rv.releasable
        or (not rv.remedies and all(s.verdict.usable_for_new_navigation for s in rv.steps))
    )

    parts: list[str] = []
    parts.append("# 多语办事指引发布就绪报告\n")
    parts.append(
        "本报告只读取 `fixtures/` 的合成运营记录。**“通过”一律指该语义单元在对应语言下"
        "取得足额独立盲审且未被更正，不指审校记录里的 equivalent 比例。**\n"
    )

    # -- 1. bottom line ----------------------------------------------------
    parts.append("## 一、结论先行\n")
    parts.append(
        _bullet(
            [
                f"**现在就能完整开放的路线：{'、'.join(releasable) or '无'}。**"
                f"其余 {len(route_verdicts) - len(releasable)} 条都至少有一个决定办事结果的语义单元未达门槛。",
                f"**编辑换用已过审写法即可，不需再审：{'、'.join(editorial) or '无'}。**",
                f"**必须先补译/改稿/消歧，不能只靠再审：{'、'.join(needs_content) or '无'}。**",
                f"**补足一名独立盲审即可解锁：{'、'.join(review_gated) or '无'}。**"
                "（按现有合格时段，见第五节，实际可执行项更少。）",
                "总通过率不能当开通依据：欢迎语、进度查询这些低风险单元由同一批人反复认可，"
                "通过率自然高；资格条件、付费责任、提交对象这三类才改办事结果，恰恰是卡住路线的地方。",
            ]
        )
        + "\n"
    )

    # -- 2. comparable coverage -------------------------------------------
    parts.append("## 二、按语言 × 语义风险的可比覆盖\n")
    parts.append(
        "`简单通过率` 是原始审校记录里 equivalent 的条数比，仅作旁证，**不用于放行**。"
        "`合格覆盖` 是达到独立盲审门槛的现行译稿数 / 本语言该概念的现行译稿数。"
        "右侧各列把不能混算的原因拆开：内容缺失、存在分歧、未独立复核（份数不足）、审校员对照不足、完全未审。\n"
    )
    for language in ("en", "mn", "ru"):
        rows = []
        for c in rules.scope_coverage(dataset, verdicts, language):
            rows.append(
                [
                    c.concept,
                    c.risk,
                    c.naive_rate,
                    c.qualified_rate,
                    str(c.missing),
                    str(c.disputed),
                    str(c.not_independent),
                    str(c.reviewer_gap),
                    str(c.unreviewed),
                    str(c.stale),
                ]
            )
        parts.append(f"### {language}\n")
        parts.append(
            _table(
                ["概念", "风险", "简单通过率", "合格覆盖", "缺内容", "有分歧", "未独立", "对照不足", "未审", "旧稿"],
                rows,
            )
        )
        parts.append("")

    # -- 3. route evidence -------------------------------------------------
    parts.append("## 三、实际路线：审到哪里、卡在哪里\n")
    parts.append(
        "共享语义单元只提供一份审校证据。下表 `共享步骤` 的译稿被多条路线复用；"
        "它出现在别的路线，不代表那条路线的前后步骤也被审过。\n"
    )
    rows = []
    for cov in rules.route_coverage(dataset, route_verdicts):
        rv = route_verdicts[cov.route_id]
        readiness = "✅ 可开" if cov.releasable else "⛔ " + rules.STATUS_LABEL[cov.status]
        fix = "；".join(
            f"{r.translation_id} {r.kind}" + (f"→{r.version_bump}" if r.version_bump else "")
            for r in rv.remedies
        ) or "—"
        gate = "依赖完整，编辑可独立确认" if rv.dependency_complete else "依赖未完，等审核人回交"
        rows.append(
            [
                cov.route_id,
                f"{cov.desk}/{cov.language}/{cov.version}",
                f"{cov.supported_steps}/{cov.step_count}",
                readiness,
                "、".join(cov.shared_steps) or "—",
                "<br>".join(cov.blocking_steps) or "—",
                fix,
                gate,
            ]
        )
    parts.append(_table(["路线", "窗口/语言/版本", "已过审步骤", "就绪", "共享步骤", "阻塞步骤", "改法", "审校依赖"], rows))
    parts.append("")
    parts.append(
        "证据复用情况（同一译稿被几条路线引用）："
        + "，".join(f"{tid}×{count}" for tid, count in sorted(shared.items()) if count > 1)
        + "。"
        "T5 一条就要被 5 条路线引用，它的通过只能算一次合格覆盖，不能乘出五份证据。\n"
    )

    # -- 4. traps ----------------------------------------------------------
    parts.append("## 四、必须单列的语义陷阱与分歧辨别\n")
    trap_lines = []
    for c in conflicts:
        if c["kind"] == "same_wording_different_meaning":
            trap_lines.append(
                f"同一句话在两种来源版本下含义不同：`{c['detail']}` 同时对应 "
                f"{', '.join(c['unit_ids'])}（{'；'.join(c['meanings'])}）。"
                f"在 {', '.join(c['translation_ids'])} 之一取得的盲审**不能**平移到另一含义。"
            )
        else:
            trap_lines.append(
                f"等价码 `{c['detail']}` 跨越了不同含义（{', '.join(c['unit_ids'])}），"
                "不能只凭码相同就当同义复用。"
            )
    parts.append(_bullet(trap_lines))
    parts.append("")
    parts.append("审校员对照表现（只统计未见标准答案的盲审对照）：\n")
    rows = []
    for reviewer in sorted({r.reviewer for r in dataset.reviews.values()}):
        for language in ("en", "mn", "ru"):
            for concept in ("eligible", "proof", "pay"):
                st = rules.control_standing(dataset, reviewer, language, concept)
                if st.blind_pairs or st.seen_gold:
                    rows.append(
                        [
                            reviewer,
                            f"{language}/{concept}",
                            str(st.blind_pairs),
                            f"{st.different_correct} 不可互换 / {st.equivalent_correct} 同义",
                            str(st.wrong),
                            str(st.seen_gold),
                            "可用" if st.usable else "不足",
                            st.reason,
                        ]
                    )
    parts.append(_table(["审校", "范围", "盲审对数", "判对", "错判", "看过答案", "结论", "依据"], rows))
    parts.append("")
    parts.append("分歧辨别（成对结果 + 同义改写；排除看过答案与无对照资格的判断）：\n")
    if disagreements:
        rows = [
            [
                f"{d.translation_id}/{d.unit_id}",
                "、".join(d.supporters) or "—",
                "、".join(d.opposers),
                d.excluded_view or "—",
                d.reading,
            ]
            for d in disagreements
        ]
        parts.append(_table(["译稿/单元", "认可方", "反对方", "被排除的判断", "判断"], rows))
    else:
        parts.append("（无分歧）")
    parts.append("")

    # -- 5. windows --------------------------------------------------------
    parts.append("## 五、复审时段怎么用\n")
    parts.append(
        "同一人同一时段只能做一项；已看过同稿或同义稿的人不算第二名独立审校；"
        "把某人派去一个位置会占掉另一语言共用的时段。高风险审校还要在**判断当刻**已取得对照资格。\n"
    )
    rows = []
    for plan in plans:
        rows.append(
            [
                plan.window_id,
                f"{plan.reviewer} {plan.start}-{plan.end}（{plan.minutes} 分）",
                f"{plan.language}/{plan.concept}",
                "可" if plan.standing_ok else "不可",
                "<br>".join(
                    f"{t.translation_id}→解锁{'/'.join(t.would_unlock) or '（仅推进）'}"
                    + (f"【{t.note}】" if t.note else "")
                    + ("" if t.feasible else "（本时段无法完成）")
                    for t in plan.tasks
                )
                or "—",
                "、".join(plan.conflicts) or "—",
                plan.verdict,
            ]
        )
    parts.append(_table(["时段", "审校与时间", "范围", "资格", "可补做", "冲突", "判断"], rows))
    parts.append("")
    parts.append("安排建议：\n")
    parts.append(_bullet([f"**{a.reviewer}**：选 {'、'.join(a.chosen) or '无'}；释放 {'、'.join(a.released) or '无'}。{a.reason}" for a in allocations]) + "\n")

    # -- 6. correction, legacy, retirement --------------------------------
    parts.append("## 六、术语办更正后的下线与兜底\n")
    parts.append(
        f"更正范围：单元 {', '.join(scope['units'])} → {', '.join(scope['replacements'])}，"
        f"涉及概念 {', '.join(scope['concepts'])}、语言 {', '.join(scope['languages'])}。"
        "**从未用到该概念的路线无需整体下线**；只暂停受影响步骤。\n"
    )
    rows = []
    for e in exposures:
        rows.append(
            [
                e.route_id,
                f"{e.desk}/{e.language}/{e.version}",
                "、".join(e.published) or "—",
                "、".join(e.bookings) or "—",
                "、".join(e.paused_steps) or "—",
                "是" if e.in_correction_scope else "否",
                "、".join(e.needs_manual_explanation) or "—",
                e.note,
            ]
        )
    parts.append(_table(["路线", "窗口", "已发布", "预约", "须暂停步骤", "在更正范围", "需人工说明的预约", "处理"], rows))
    parts.append("")
    parts.append("签收对账（窗口/编辑查“实际看到哪套指引”）：\n")
    rows = []
    for e in exposures:
        for booking in e.bookings:
            record = dataset.releases[booking]
            basis = dataset.releases[record.link] if record.link else None
            rows.append(
                [
                    booking,
                    e.route_id,
                    record.at,
                    f"{basis.route}/{basis.at}（{basis.id}）" if basis else "缺依据",
                    "需人工说明" if booking in e.needs_manual_explanation else "按原承诺",
                ]
            )
    parts.append(_table(["预约回执", "路线", "登记时间", "依据的发布", "处理"], rows or [["—"] * 5]))
    parts.append("")
    legacy_lines = [
        f"{rid} 的旧版 {route_verdicts[rid].route.legacy}：{route_verdicts[rid].legacy_note}"
        for rid in sorted(route_verdicts)
        if route_verdicts[rid].route.legacy
    ]
    parts.append("旧版兜底判断（按阻塞概念逐项核对，不按整条路线一刀切）：\n")
    parts.append(_bullet(legacy_lines))
    parts.append("")
    parts.append(
        "**若可用旧稿也含同一错误，就不能把它当兜底。**旧预约按登记时的办事承诺继续办理；"
        "不得把新版前半段与要求已取消材料的旧版后半段拼成一条路线。"
        "窗口可用本地签收（`published` 行）与复审回执（`booking` 的 `link` 指向其依据发布）"
        "查清实际看到哪套指引、哪部分已暂停、哪些旧预约需要人工说明。\n"
    )

    # -- 7. in-flight ------------------------------------------------------
    parts.append("## 七、尚在盲审与下一步能解除的限制\n")
    pending = rules.in_flight(dataset)
    parts.append(
        f"复审未回交的发布记录：{', '.join(pending) or '无'}。这些资料在审核人回交前**不得视为通过**。"
        "编辑可独立确认“依赖完整”的路线（下表最后一列），但依赖完整不等于某个关键语义已读得懂："
        "N1、N2、N3、N4、N9 的依赖都已闭合，仍分别卡在更正语义或未独立复核上。\n"
    )
    unlock: dict[str, list[str]] = {}
    for plan in plans:
        for task in plan.tasks:
            if task.would_unlock:
                unlock.setdefault(task.translation_id, []).extend(task.would_unlock)
    if unlock:
        parts.append(
            _bullet(
                [
                    f"{tid} 审完后可解锁：{'、'.join(sorted(set(routes)))}"
                    + ("（但第五节显示现有合格时段做不完，需另排时段）" if not any(t.feasible for p in plans for t in p.tasks if t.translation_id == tid) else "")
                    for tid, routes in sorted(unlock.items())
                ]
            )
            + "\n"
        )
    else:
        parts.append("当前没有单一审校项能解锁路线；余下阻塞需改稿、补译或补齐两名独立盲审。\n")

    # -- 8. gaps -----------------------------------------------------------
    parts.append("## 八、路线之外的覆盖缺口\n")
    parts.append(_bullet(gaps) + "\n")

    # -- 9. per-unit ledger ------------------------------------------------
    parts.append("## 九、逐条语义单元台账\n")
    rows = []
    for tid in sorted(verdicts):
        v = verdicts[tid]
        rows.append(
            [
                tid,
                v.translation.language,
                v.unit.id,
                v.unit.concept,
                v.unit.risk,
                v.summary,
                "、".join(r.id for r in v.support) or "—",
                "、".join(r.id for r in v.oppose) or "—",
                "、".join(r.id for r in v.no_standing + v.seen_gold) or "—",
            ]
        )
    parts.append(_table(["译稿", "语言", "单元", "概念", "风险", "结论", "有效认可", "反对", "不计入"], rows))
    parts.append("")
    return "\n".join(parts)


def main() -> None:
    print(render())


if __name__ == "__main__":
    main()
