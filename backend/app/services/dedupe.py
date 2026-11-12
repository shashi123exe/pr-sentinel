"""Deterministic finding fingerprinting, deduplication, and static/AI merging."""

import hashlib
import logging
import re
from typing import Any

from app.db.models import Severity, Source

logger = logging.getLogger(__name__)

SEVERITY_RANK: dict[str, int] = {
    Severity.CRITICAL.value: 0,
    Severity.HIGH.value: 1,
    Severity.MEDIUM.value: 2,
    Severity.LOW.value: 3,
}

RANK_TO_SEVERITY: dict[int, str] = {v: k for k, v in SEVERITY_RANK.items()}


STOP_WORDS: set[str] = {
    "the",
    "a",
    "an",
    "in",
    "on",
    "of",
    "for",
    "with",
    "at",
    "by",
    "from",
    "to",
    "and",
    "or",
    "is",
    "are",
    "was",
    "were",
    "be",
    "this",
    "that",
}


def normalize_title_for_fingerprint(title: str) -> str:
    """Normalize headline title by lowercasing, stripping punctuation, and spaces."""
    clean = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", clean).strip()


def compute_finding_fingerprint(
    file_path: str,
    line_start: int,
    category: str,
    title: str,
    rule_id: str = "",
) -> str:
    """Compute deterministic SHA-256 fingerprint for a finding.

    Fingerprint formula:
    sha256(normalized_file_path | line_bucket | category | normalized_title | rule_id)
    where line_bucket = line_start // 5.
    Preserves repository path case sensitivity.
    """
    norm_path = file_path.replace("\\", "/").strip().lstrip("/")
    line_bucket = str(line_start // 5) if line_start is not None else "0"
    norm_cat = str(category).strip().lower()
    norm_title = normalize_title_for_fingerprint(title)
    norm_rule = str(rule_id).strip().lower()

    payload = f"{norm_path}|{line_bucket}|{norm_cat}|{norm_title}|{norm_rule}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def title_token_similarity(title1: str, title2: str) -> float:
    """Calculate Jaccard similarity of significant word tokens between two titles.

    Strips static tool name prefixes (Bandit:, Ruff:, ESLint:) and common stopwords.
    """
    clean1 = re.sub(
        r"^(bandit|ruff|eslint|flake8|pylint)\s*:\s*", "", title1, flags=re.I
    )
    clean2 = re.sub(
        r"^(bandit|ruff|eslint|flake8|pylint)\s*:\s*", "", title2, flags=re.I
    )

    tokens1 = {
        w
        for w in re.sub(r"[^\w\s]", "", clean1.lower()).split()
        if len(w) > 2 and w not in STOP_WORDS
    }
    tokens2 = {
        w
        for w in re.sub(r"[^\w\s]", "", clean2.lower()).split()
        if len(w) > 2 and w not in STOP_WORDS
    }
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    return len(intersection) / len(union)


def lines_overlap_or_adjacent(
    f1_start: int,
    f1_end: int,
    f2_start: int,
    f2_end: int,
    max_distance: int = 2,
) -> bool:
    """Determine whether two findings have overlapping or adjacent line spans."""
    # Check overlapping intervals
    if max(f1_start, f2_start) <= min(f1_end, f2_end):
        return True
    # Interval gap
    if f1_end < f2_start:
        return (f2_start - f1_end) <= max_distance
    if f2_end < f1_start:
        return (f1_start - f2_end) <= max_distance
    return False


def deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate findings, merge Static+AI duplicates, and sort deterministically.

    Deduplication rules:
    1. Identical fingerprints: Keep finding with higher confidence.
       Preserve highest severity across the duplicates.
    2. Static + AI duplicates: Merge overlapping static and AI findings on the
       same file, category, and related titles into the static finding.
    3. Severity preservation: Never lose a Critical/High finding during merge.
    4. Deterministic sort: Severity (critical -> low), Confidence (high -> low),
       File path, Line start.
    """
    if not findings:
        return []

    # Assign calculated fingerprints to all findings
    with_fps: list[dict[str, Any]] = []
    for item in findings:
        entry = dict(item)
        fp = compute_finding_fingerprint(
            file_path=entry["file_path"],
            line_start=entry["line_start"],
            category=entry["category"],
            title=entry["title"],
            rule_id=entry.get("rule_id", "ai_review"),
        )
        entry["fingerprint"] = fp
        with_fps.append(entry)

    # 1. Group by identical fingerprints
    fp_groups: dict[str, list[dict[str, Any]]] = {}
    for item in with_fps:
        fp_groups.setdefault(item["fingerprint"], []).append(item)

    collapsed_fps: list[dict[str, Any]] = []
    for group in fp_groups.values():
        if len(group) == 1:
            collapsed_fps.append(group[0])
            continue

        # Sort group by confidence desc, explanation length desc, then canonical
        # deterministic tie-breakers on explanation, evidence, suggested_fix, line_end
        group.sort(
            key=lambda x: (
                float(x.get("confidence", 0.0)),
                len(x.get("explanation", "")),
                str(x.get("explanation", "")),
                len(str(x.get("evidence", ""))),
                str(x.get("evidence", "")),
                str(x.get("suggested_fix", "") or ""),
                int(x.get("line_end", 0) or 0),
            ),
            reverse=True,
        )
        winner = dict(group[0])

        # Preserve highest severity across all members in the group
        min_rank = min(SEVERITY_RANK.get(member["severity"], 3) for member in group)
        winner["severity"] = RANK_TO_SEVERITY[min_rank]

        # Preserve highest confidence
        max_conf = max(float(member.get("confidence", 0.0)) for member in group)
        winner["confidence"] = max_conf

        # Prefer static source if any member is static
        if any(member.get("source") == Source.STATIC.value for member in group):
            static_member = next(
                m for m in group if m.get("source") == Source.STATIC.value
            )
            winner["source"] = Source.STATIC.value
            winner["tool_name"] = static_member["tool_name"]
            winner["rule_id"] = static_member["rule_id"]

        collapsed_fps.append(winner)

    # 2. Static + AI Duplicate Merging
    static_items = [f for f in collapsed_fps if f.get("source") == Source.STATIC.value]
    ai_items = [f for f in collapsed_fps if f.get("source") == Source.AI.value]

    merged_static: list[dict[str, Any]] = []
    ai_dropped_indices: set[int] = set()

    for s_item in static_items:
        s_res = dict(s_item)
        s_norm_path = s_res["file_path"].replace("\\", "/").strip().lstrip("/")

        for idx, a_item in enumerate(ai_items):
            if idx in ai_dropped_indices:
                continue

            a_norm_path = a_item["file_path"].replace("\\", "/").strip().lstrip("/")
            if s_norm_path != a_norm_path:
                continue
            if s_res["category"] != a_item["category"]:
                continue

            # Check line overlap or adjacency
            if not lines_overlap_or_adjacent(
                s_res["line_start"],
                s_res["line_end"],
                a_item["line_start"],
                a_item["line_end"],
            ):
                continue

            # Check title similarity
            sim = title_token_similarity(s_res["title"], a_item["title"])
            if sim >= 0.35:
                # Merge into static finding
                ai_dropped_indices.add(idx)

                # Preserve highest severity
                s_rank = SEVERITY_RANK.get(s_res["severity"], 3)
                a_rank = SEVERITY_RANK.get(a_item["severity"], 3)
                if a_rank < s_rank:
                    s_res["severity"] = RANK_TO_SEVERITY[a_rank]

                # Preserve highest confidence
                s_res["confidence"] = max(
                    float(s_res.get("confidence", 1.0)),
                    float(a_item.get("confidence", 1.0)),
                )

                # Merge AI explanation into static explanation if useful
                a_expl = a_item.get("explanation", "").strip()
                if a_expl and a_expl not in s_res["explanation"]:
                    s_res["explanation"] = (
                        f"{s_res['explanation']}\n\n[AI Review Confirmation]: {a_expl}"
                    )

                # Adopt suggested_fix if static has none
                if not s_res.get("suggested_fix") and a_item.get("suggested_fix"):
                    s_res["suggested_fix"] = a_item["suggested_fix"]

                logger.info(
                    "Merged static finding [%s] with AI duplicate [%s] on %s:%d",
                    s_res["rule_id"],
                    a_item["title"],
                    s_res["file_path"],
                    s_res["line_start"],
                )

        merged_static.append(s_res)

    surviving_ai = [
        a for idx, a in enumerate(ai_items) if idx not in ai_dropped_indices
    ]
    combined = merged_static + surviving_ai

    # 3. Deterministic Sort FIRST:
    # Severity rank (critical -> low), Confidence (high -> low),
    # File path, Line start, Title, Category, Rule ID, Fingerprint, Explanation
    combined.sort(
        key=lambda f: (
            SEVERITY_RANK.get(f.get("severity", "low"), 3),
            -float(f.get("confidence", 1.0)),
            str(f.get("file_path", "")),
            int(f.get("line_start", 0) or 0),
            str(f.get("title", "")),
            str(f.get("category", "")),
            str(f.get("rule_id", "")),
            str(f.get("fingerprint", "")),
            str(f.get("explanation", "")),
        )
    )

    # 4. Deterministic collision resolution for unique (review_id, fingerprint)
    seen_fps: set[str] = set()
    for idx, item in enumerate(combined):
        fp = item["fingerprint"]
        if fp in seen_fps:
            # Suffix with index to ensure unique fingerprint per database constraint
            resolved_fp = hashlib.sha256(f"{fp}:{idx}".encode()).hexdigest()
            item["fingerprint"] = resolved_fp
        seen_fps.add(item["fingerprint"])

    return combined
