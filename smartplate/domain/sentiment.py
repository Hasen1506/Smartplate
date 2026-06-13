"""Sentiment discovery on a LOCAL lexicon — zero API cost (FEASIBILITY.md §2, §6).

The brainstorm's differentiator is surfacing *quality* not popularity (§3.1). We
score short review snippets with a small VADER-style lexicon. No model server,
no per-call cost. The LLMBrain can override this later, but it is never required.
"""
import re

POSITIVE = {
    "amazing": 2.0, "delicious": 1.8, "fresh": 1.3, "perfect": 1.9, "love": 1.6,
    "great": 1.4, "tasty": 1.4, "hot": 0.8, "generous": 1.2, "consistent": 1.3,
    "authentic": 1.4, "value": 1.1, "best": 1.7, "fast": 0.9, "quality": 1.3,
}
NEGATIVE = {
    "cold": -1.4, "stale": -1.9, "bland": -1.5, "soggy": -1.6, "late": -1.2,
    "tiny": -1.1, "oily": -1.0, "overpriced": -1.5, "rude": -1.4, "worst": -2.0,
    "missing": -1.6, "spilled": -1.7, "raw": -1.8, "sick": -2.0, "dirty": -1.9,
}
NEGATIONS = {"not", "no", "never", "barely", "hardly"}


def score_text(text: str) -> float:
    """Return a sentiment score in roughly [-1, 1]."""
    tokens = re.findall(r"[a-z']+", text.lower())
    total = 0.0
    hits = 0
    for i, tok in enumerate(tokens):
        val = POSITIVE.get(tok, 0.0) + NEGATIVE.get(tok, 0.0)
        if val:
            if i > 0 and tokens[i - 1] in NEGATIONS:
                val = -val
            total += val
            hits += 1
    if not hits:
        return 0.0
    # squash to [-1, 1]
    avg = total / hits
    return max(-1.0, min(1.0, avg / 2.0))


def aggregate(reviews: list[str]) -> dict:
    if not reviews:
        return {"score": 0.0, "n": 0, "label": "no reviews"}
    scores = [score_text(r) for r in reviews]
    mean = sum(scores) / len(scores)
    label = "loved" if mean > 0.35 else "liked" if mean > 0.1 else \
            "mixed" if mean > -0.1 else "disliked"
    return {"score": round(mean, 3), "n": len(reviews), "label": label}
