"""Core grounding/hallucination audit engine for HALLUMARK.

The engine answers one question for every RAG record: *is the model's answer
actually supported by the context that was retrieved for it?*

Approach (all heuristic + deterministic, stdlib only):

1. Split the answer into atomic claims (sentence-level, with light clause
   splitting on conjunctions).
2. For each claim, compute a *support score* against the retrieved context:
   - Lexical overlap of content tokens (Jaccard over the best-matching context
     sentence, so a claim supported by ONE chunk isn't diluted by others).
   - A numeric-consistency check: every number/year/percentage in the claim must
     appear in the context, else the claim is flagged as a numeric hallucination
     (a common and high-severity RAG failure mode).
   - A negation-flip check: if the claim negates something the context asserts
     (or vice versa) the support is penalized.
3. A claim with support below the threshold is an *unsupported claim*
   (a hallucination finding).
4. Aggregate per record into faithfulness (fraction of grounded claims) and
   context-utilization (how much of the answer's signal came from context).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List

# --------------------------------------------------------------------------- #
# Tokenization helpers
# --------------------------------------------------------------------------- #

_STOPWORDS = frozenset("""
a an and are as at be been being but by for from had has have he her him his how
i if in into is it its of on or our that the their them then there these they
this to was we were what when where which who will with would you your about
over under can could should may might must do does did not no nay also than
such into across per via given each any some most more very just only
""".split())

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'_-]*")
# Numbers: integers, decimals, percentages, years, comma-grouped, with units.
_NUM_RE = re.compile(r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\$?\d+(?:\.\d+)?%?")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])|\n+")
# Light clause split so compound answers are scored claim-by-claim.
_CLAUSE_SPLIT_RE = re.compile(r"\s+(?:and also|; however|; but|; and)\s+", re.I)

_NEGATIONS = frozenset(
    "not no never none cannot can't won't isn't aren't wasn't weren't "
    "doesn't don't didn't without nor neither".split()
)


def _content_tokens(text: str) -> List[str]:
    """Lowercased content tokens with stopwords removed."""
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if t.lower() not in _STOPWORDS and len(t) > 1
    ]


def _normalize_num(raw: str) -> str:
    """Normalize a numeric token so '1,200' == '1200' and '$5.0' == '5'."""
    s = raw.replace(",", "").replace("$", "")
    is_pct = s.endswith("%")
    s = s.rstrip("%")
    try:
        f = float(s)
        s = str(int(f)) if f.is_integer() else ("%g" % f)
    except ValueError:
        pass
    return s + ("%" if is_pct else "")


def _numbers(text: str) -> List[str]:
    return [_normalize_num(m.group(0)) for m in _NUM_RE.finditer(text)]


def _split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text or "") if p.strip()]
    return parts


def split_claims(answer: str) -> List[str]:
    """Split an answer into atomic claims (sentences + light clause splitting)."""
    claims: List[str] = []
    for sent in _split_sentences(answer):
        for clause in _CLAUSE_SPLIT_RE.split(sent):
            clause = clause.strip(" .;,")
            if len(_content_tokens(clause)) >= 1:
                claims.append(clause)
    # Fallback: a short single-fact answer with no sentence punctuation.
    if not claims and answer and _content_tokens(answer):
        claims.append(answer.strip())
    return claims


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass
class Claim:
    text: str
    support: float            # 0.0 - 1.0
    grounded: bool
    best_context_idx: int     # which context chunk best supported it (-1 = none)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecordAudit:
    record_id: str
    question: str
    answer: str
    n_claims: int
    n_grounded: int
    n_unsupported: int
    faithfulness: float        # fraction of claims grounded in context
    context_utilization: float # fraction of context chunks that supported a claim
    answer_relevance: float    # overlap of answer with the question's intent
    passed: bool
    claims: List[Claim] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["claims"] = [c.to_dict() for c in self.claims]
        return d


@dataclass
class AuditReport:
    n_records: int
    n_passed: int
    n_failed: int
    total_claims: int
    total_unsupported: int
    mean_faithfulness: float
    mean_context_utilization: float
    mean_answer_relevance: float
    threshold: float
    records: List[RecordAudit] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return self.n_failed > 0 or self.total_unsupported > 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["records"] = [r.to_dict() for r in self.records]
        d["has_findings"] = self.has_findings
        return d


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _overlap_coverage(claim_tokens: List[str], ctx_tokens: set) -> float:
    """Fraction of the claim's content tokens that appear in the context."""
    if not claim_tokens:
        return 0.0
    present = sum(1 for t in set(claim_tokens) if t in ctx_tokens)
    return present / len(set(claim_tokens))


def _score_claim(
    claim: str,
    context_chunks: List[str],
    threshold: float,
) -> Claim:
    """Score a single claim against each context chunk; keep the best match."""
    claim_tokens = _content_tokens(claim)
    claim_nums = _numbers(claim)
    claim_neg = any(t in _NEGATIONS for t in claim.lower().split())

    best_support = 0.0
    best_idx = -1
    reasons: List[str] = []

    for idx, chunk in enumerate(context_chunks):
        # Score against the single best sentence in the chunk so a claim that is
        # supported by one line isn't diluted by a long chunk.
        chunk_sents = _split_sentences(chunk) or [chunk]
        chunk_full_tokens = set(_content_tokens(chunk))
        local_best = 0.0
        for sent in chunk_sents:
            sent_tokens = _content_tokens(sent)
            jac = _jaccard(claim_tokens, sent_tokens)
            cov = _overlap_coverage(claim_tokens, set(sent_tokens))
            # Blend: coverage matters more (did the context actually contain the
            # claim's terms?) but Jaccard guards against trivial matches.
            local_best = max(local_best, 0.65 * cov + 0.35 * jac)
        # Coverage against the whole chunk catches multi-sentence support.
        local_best = max(
            local_best,
            0.5 * _overlap_coverage(claim_tokens, chunk_full_tokens),
        )
        if local_best > best_support:
            best_support = local_best
            best_idx = idx

    support = best_support

    # Global coverage check: if the majority of the claim's content tokens are
    # absent from ALL context combined, the claim introduces facts not grounded
    # in any retrieved chunk (a hallucination of entities/facts).  Cap support
    # below the pass threshold so such claims are always flagged.
    if claim_tokens:
        all_ctx_tokens: set = set()
        for chunk in context_chunks:
            all_ctx_tokens.update(_content_tokens(chunk))
        global_cov = _overlap_coverage(claim_tokens, all_ctx_tokens)
        if global_cov < 0.5:
            # More than half the claim's terms are absent from the entire
            # retrieved context — almost certainly a fabricated claim.
            support = min(support * 0.3, 0.15)
            if not reasons:
                reasons.append(
                    "most claim terms absent from retrieved context "
                    "(possible entity/fact fabrication)"
                )

    # Numeric consistency: any number in the claim must exist somewhere in ctx.
    if claim_nums:
        ctx_nums = set()
        for chunk in context_chunks:
            ctx_nums.update(_numbers(chunk))
        missing = [n for n in claim_nums if n not in ctx_nums]
        if missing:
            # A fabricated numeric value is a high-severity hallucination; cap
            # the support well below any reasonable pass threshold so the claim
            # is always flagged as unsupported.
            support = min(support * 0.2, 0.15)
            reasons.append(
                "numeric value(s) not found in context: "
                + ", ".join(sorted(set(missing)))
            )

    # Negation flip: claim negates but best-matching context does not (or vice
    # versa) -> the polarity disagrees, penalize strongly.
    if best_idx >= 0:
        ctx_neg = any(
            t in _NEGATIONS for t in context_chunks[best_idx].lower().split()
        )
        if claim_neg != ctx_neg and best_support > 0.3:
            # Polarity inversion is a semantic hallucination; cap support below
            # the pass threshold regardless of lexical overlap.
            support = min(support * 0.25, 0.15)
            reasons.append("negation/polarity mismatch with best-matching context")

    grounded = support >= threshold
    if not grounded and not reasons:
        if best_support < 0.15:
            reasons.append("no meaningful overlap with any retrieved context chunk")
        else:
            reasons.append("weak support: below grounding threshold")

    return Claim(
        text=claim,
        support=round(support, 4),
        grounded=grounded,
        best_context_idx=best_idx,
        reasons=reasons,
    )


def audit_record(
    record: Dict[str, Any],
    threshold: float = 0.3,
    pass_faithfulness: float = 0.8,
) -> RecordAudit:
    """Audit one RAG record.

    A record is a dict with keys:
        id (optional), question, answer, contexts (list of strings).
    A single 'context' string is also accepted.
    """
    rid = str(record.get("id", record.get("record_id", "")))
    question = str(record.get("question", record.get("query", "")))
    answer = str(record.get("answer", record.get("response", "")))

    contexts = record.get("contexts", record.get("context", []))
    if isinstance(contexts, str):
        contexts = [contexts]
    contexts = [c for c in contexts if isinstance(c, str) and c.strip()]

    claims = split_claims(answer)
    scored = [_score_claim(c, contexts, threshold) for c in claims]

    n_claims = len(scored)
    n_grounded = sum(1 for c in scored if c.grounded)
    n_unsupported = n_claims - n_grounded
    faithfulness = (n_grounded / n_claims) if n_claims else 1.0

    used_idxs = {
        c.best_context_idx
        for c in scored
        if c.grounded and c.best_context_idx >= 0
    }
    context_utilization = (len(used_idxs) / len(contexts)) if contexts else 0.0

    # Answer relevance: does the answer engage the question's content terms?
    q_tokens = _content_tokens(question)
    a_tokens = _content_tokens(answer)
    answer_relevance = _overlap_coverage(q_tokens, set(a_tokens)) if q_tokens else 0.0

    passed = faithfulness >= pass_faithfulness and n_unsupported == 0

    return RecordAudit(
        record_id=rid,
        question=question,
        answer=answer,
        n_claims=n_claims,
        n_grounded=n_grounded,
        n_unsupported=n_unsupported,
        faithfulness=round(faithfulness, 4),
        context_utilization=round(context_utilization, 4),
        answer_relevance=round(answer_relevance, 4),
        passed=passed,
        claims=scored,
    )


def audit_records(
    records: List[Dict[str, Any]],
    threshold: float = 0.3,
    pass_faithfulness: float = 0.8,
) -> AuditReport:
    audits = [
        audit_record(r, threshold, pass_faithfulness)
        for r in records
        if isinstance(r, dict)
    ]
    n = len(audits)
    n_passed = sum(1 for a in audits if a.passed)
    total_claims = sum(a.n_claims for a in audits)
    total_unsupported = sum(a.n_unsupported for a in audits)

    def _mean(vals: List[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return AuditReport(
        n_records=n,
        n_passed=n_passed,
        n_failed=n - n_passed,
        total_claims=total_claims,
        total_unsupported=total_unsupported,
        mean_faithfulness=_mean([a.faithfulness for a in audits]),
        mean_context_utilization=_mean([a.context_utilization for a in audits]),
        mean_answer_relevance=_mean([a.answer_relevance for a in audits]),
        threshold=threshold,
        records=audits,
    )


# --------------------------------------------------------------------------- #
# Input loading
# --------------------------------------------------------------------------- #


def load_records(path: str) -> List[Dict[str, Any]]:
    """Load RAG records from a .json (list/object) or .jsonl file.

    Raises:
        FileNotFoundError: if *path* does not exist.
        OSError: if the file cannot be read (permissions, directory, etc.).
        UnicodeDecodeError: if the file is not valid UTF-8.
        ValueError: if the content cannot be parsed as JSON/JSONL.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except UnicodeDecodeError as exc:
        raise UnicodeDecodeError(
            exc.encoding, exc.object, exc.start, exc.end,
            "file %r is not valid UTF-8: %s" % (path, exc.reason),
        ) from exc
    return parse_records(text)


def parse_records(text: str) -> List[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    # Try a single JSON document first (list or object).
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            # Allow {"records": [...]} or a single record.
            if isinstance(data.get("records"), list):
                return [d for d in data["records"] if isinstance(d, dict)]
            return [data]
    except json.JSONDecodeError:
        pass
    # Fall back to JSONL.
    records: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "malformed JSON on line %d: %s" % (lineno, exc)
            ) from exc
        if isinstance(obj, dict):
            records.append(obj)
    return records
