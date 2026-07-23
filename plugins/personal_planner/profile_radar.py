"""Pure, explainable profile-to-monitor proposal generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any


GRADUATE_TOKENS = (
    "研究生",
    "硕士",
    "博士",
    "master",
    "graduate",
    "phd",
)
COMPUTING_TOKENS = (
    "计算机",
    "软件",
    "人工智能",
    "网络空间",
    "computer",
    "software",
    "artificial intelligence",
    "cyber",
)
CAREER_TOKENS = (
    "实习",
    "就业",
    "找工作",
    "校招",
    "秋招",
    "intern",
    "career",
    "job",
)
RESEARCH_TOKENS = (
    "论文",
    "发表",
    "投稿",
    "科研",
    "paper",
    "publication",
    "research",
)
LANGUAGE_EXAM_TOKENS = (
    "六级",
    "四级",
    "四六级",
    "cet-4",
    "cet-6",
    "cet4",
    "cet6",
)


def build_monitor_proposals(
    *,
    facts: list[dict[str, Any]],
    goals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic proposals from confirmed, non-sensitive facts."""
    usable_facts = [
        fact
        for fact in facts
        if fact["status"] == "confirmed"
        and fact["privacy"] == "standard"
    ]
    fact_map = {
        (fact["category"], fact["fact_key"]): fact
        for fact in usable_facts
    }
    school = _fact_value(fact_map, "education", "school")
    degree = _fact_value(fact_map, "education", "degree")
    field = _fact_value(fact_map, "education", "field")
    graduation_year = _fact_value(
        fact_map, "education", "graduation_year"
    )
    goal_text = " ".join(
        " ".join(
            str(value or "")
            for value in (
                goal.get("title"),
                goal.get("area"),
                goal.get("outcome"),
            )
        )
        for goal in goals
        if goal.get("status") == "active"
    )

    proposals: list[dict[str, Any]] = []
    if school:
        evidence = [_fact_evidence(fact_map[("education", "school")])]
        proposals.append(
            _proposal(
                topic_key="institution_notices",
                category="academic_admin",
                title=f"{school} official notices",
                rationale=(
                    f"Your confirmed profile says you study at {school}; "
                    "official notices may contain registration windows, "
                    "deadlines, events, and policy changes."
                ),
                priority="high",
                cadence="daily",
                source_kinds=[
                    "official_university",
                    "official_school_department",
                ],
                query_terms=[
                    f"{school} 通知",
                    f"{school} 公告",
                    f"{school} 报名 截止",
                ],
                evidence=evidence,
            )
        )

    is_graduate = _contains(degree, GRADUATE_TOKENS)
    if is_graduate:
        evidence = [_fact_evidence(fact_map[("education", "degree")])]
        if school:
            evidence.append(
                _fact_evidence(fact_map[("education", "school")])
            )
        if graduation_year:
            evidence.append(
                _fact_evidence(
                    fact_map[("education", "graduation_year")]
                )
            )
        context = school or "your university"
        year_note = (
            f" Your expected graduation year is {graduation_year}."
            if graduation_year
            else ""
        )
        proposals.append(
            _proposal(
                topic_key="graduate_milestones",
                category="academic_admin",
                title="Graduate training and degree milestones",
                rationale=(
                    "Graduate study has long-lead milestones such as training "
                    "plans, scholarships, proposal reviews, mid-term checks, "
                    f"thesis review, and degree applications.{year_note}"
                ),
                priority="high",
                cadence="daily",
                source_kinds=[
                    "official_graduate_school",
                    "official_school_department",
                ],
                query_terms=[
                    f"{context} 研究生院 通知",
                    f"{context} 开题 中期 送审",
                    f"{context} 奖学金 学位 申请",
                ],
                evidence=evidence,
            )
        )

    is_computing = _contains(field, COMPUTING_TOKENS)
    if is_computing:
        evidence = [_fact_evidence(fact_map[("education", "field")])]
        if school:
            evidence.append(
                _fact_evidence(fact_map[("education", "school")])
            )
        context = " ".join(value for value in (school, field) if value)
        proposals.append(
            _proposal(
                topic_key="computing_opportunities",
                category="academic_opportunity",
                title="Computing research, lectures, competitions, and projects",
                rationale=(
                    "Your confirmed field is computing-related, so academic "
                    "talks, competitions, lab opportunities, and project "
                    "applications may be relevant."
                ),
                priority="normal",
                cadence="weekly",
                source_kinds=[
                    "official_school_department",
                    "official_academic_society",
                    "official_competition",
                ],
                query_terms=[
                    f"{context} 学术讲座",
                    f"{context} 竞赛",
                    f"{context} 科研项目 申报",
                ],
                evidence=evidence,
            )
        )

    career_goals = _matching_goals(goals, CAREER_TOKENS)
    if career_goals:
        evidence = [_goal_evidence(goal) for goal in career_goals]
        context = " ".join(value for value in (school, field) if value)
        proposals.append(
            _proposal(
                topic_key="career_internship",
                category="career",
                title="Internships, campus recruiting, and career events",
                rationale=(
                    "Your active goals mention internships or employment. "
                    "These opportunities often open months before their actual "
                    "start dates and benefit from early preparation."
                ),
                priority="high",
                cadence="daily",
                source_kinds=[
                    "official_employer_careers",
                    "official_university_career",
                    "official_school_department",
                ],
                query_terms=[
                    f"{context} 暑期实习",
                    f"{context} 秋招 校招",
                    f"{school or '高校'} 宣讲会 招聘",
                ],
                evidence=evidence,
            )
        )

    research_goals = _matching_goals(goals, RESEARCH_TOKENS)
    if research_goals:
        evidence = [_goal_evidence(goal) for goal in research_goals]
        proposals.append(
            _proposal(
                topic_key="research_publication",
                category="research",
                title="Paper calls, research funding, and submission deadlines",
                rationale=(
                    "Your active goals mention research or publication. "
                    "Calls for papers and project applications require "
                    "long-lead planning before the final deadline."
                ),
                priority="high",
                cadence="weekly",
                source_kinds=[
                    "official_conference",
                    "official_journal",
                    "official_research_funding",
                    "official_school_department",
                ],
                query_terms=[
                    f"{field or '计算机'} 征稿 截止",
                    f"{school or '高校'} 科研项目 申报",
                    f"{field or '学术'} conference call for papers",
                ],
                evidence=evidence,
            )
        )

    exam_goals = _matching_goals(goals, LANGUAGE_EXAM_TOKENS)
    if exam_goals:
        evidence = [_goal_evidence(goal) for goal in exam_goals]
        proposals.append(
            _proposal(
                topic_key="language_exams",
                category="exam",
                title="CET registration and exam notices",
                rationale=(
                    "Your active goals mention CET-4/CET-6. Registration "
                    "notices are usually published well before the exam and "
                    "need a distinct registration deadline and exam date."
                ),
                priority="high",
                cadence="daily",
                source_kinds=[
                    "official_exam_authority",
                    "official_university",
                ],
                query_terms=[
                    f"{school or '高校'} 四六级 报名",
                    "全国大学英语四六级考试 报名 通知",
                ],
                evidence=evidence,
            )
        )

    return proposals


def _proposal(
    *,
    topic_key: str,
    category: str,
    title: str,
    rationale: str,
    priority: str,
    cadence: str,
    source_kinds: list[str],
    query_terms: list[str],
    evidence: list[dict[str, str]],
) -> dict[str, Any]:
    signature = json.dumps(
        {
            "topic_key": topic_key,
            "evidence": evidence,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
    return {
        "proposal_key": f"{topic_key}:{digest}",
        "topic_key": topic_key,
        "category": category,
        "title": title,
        "rationale": rationale,
        "priority": priority,
        "cadence": cadence,
        "source_kinds": list(dict.fromkeys(source_kinds)),
        "query_terms": [
            value.strip()
            for value in dict.fromkeys(query_terms)
            if value.strip()
        ],
        "evidence": evidence,
    }


def _fact_value(
    fact_map: dict[tuple[str, str], dict[str, Any]],
    category: str,
    key: str,
) -> str:
    fact = fact_map.get((category, key))
    return str((fact or {}).get("fact_value") or "").strip()


def _fact_evidence(fact: dict[str, Any]) -> dict[str, str]:
    return {
        "kind": "profile_fact",
        "id": fact["id"],
        "label": f"{fact['category']}.{fact['fact_key']}={fact['fact_value']}",
    }


def _goal_evidence(goal: dict[str, Any]) -> dict[str, str]:
    return {
        "kind": "goal",
        "id": goal["id"],
        "label": goal["title"],
    }


def _matching_goals(
    goals: list[dict[str, Any]],
    tokens: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        goal
        for goal in goals
        if goal.get("status") == "active"
        and _contains(
            " ".join(
                str(value or "")
                for value in (
                    goal.get("title"),
                    goal.get("area"),
                    goal.get("outcome"),
                )
            ),
            tokens,
        )
    ]


def _contains(value: str, tokens: tuple[str, ...]) -> bool:
    normalized = str(value or "").casefold()
    return any(token.casefold() in normalized for token in tokens)
