# """Engine 3: keyword/rule baseline. Build and evaluate this BEFORE any ML."""
# import pandas as pd
# from .config import KEYWORDS, CONFIDENCE_THRESHOLD


# def load_keywords():
#     df = pd.read_csv(KEYWORDS, encoding='utf-8-sig')
#     out = {}
#     for _, r in df.iterrows():
#         out.setdefault(r["department"], []).append(str(r["keyword"]).lower())
#     return out


# def route(text: str, kw=None):
#     kw = kw or load_keywords()
#     t = text.lower()
#     scores, evidence = {}, {}
#     for dept, words in kw.items():
#         hits = [w for w in words if w in t]
#         if hits:
#             scores[dept] = len(hits)
#             evidence[dept] = hits
#     if not scores:
#         return {"top3": [], "evidence": {}, "confidence": 0.0, "abstained": True}
#     total = sum(scores.values())
#     ranked = sorted(scores.items(), key=lambda x: -x[1])[:3]
#     top3 = [{"dept": d, "score": round(s / total, 2)} for d, s in ranked]
#     conf = top3[0]["score"]
#     tie = len(ranked) > 1 and ranked[0][1] == ranked[1][1]
#     abstained = conf < CONFIDENCE_THRESHOLD or tie
#     return {"top3": top3, "evidence": {d: evidence[d] for d, _ in ranked},
#             "confidence": conf, "abstained": abstained}

"""Engine 3: keyword/rule baseline."""
import pandas as pd
from .config import KEYWORDS, CONFIDENCE_THRESHOLD


def load_keywords():
    df = pd.read_csv(KEYWORDS, encoding='utf-8-sig')
    out = {}
    for _, r in df.iterrows():
        out.setdefault(r["department"], []).append(str(r["keyword"]).lower())
    return out


def route(text: str, kw=None):
    kw = kw or load_keywords()
    t = text.lower()
    scores, evidence = {}, {}
    for dept, words in kw.items():
        hits = [w for w in words if w in t]
        if hits:
            scores[dept] = len(hits)
            evidence[dept] = hits

    # A narrowly configured ownership collision adds both owners as review
    # candidates. The collision always forces abstention; it is never an assignment.
    from .ownership import route_conflicts
    conflicts = route_conflicts(text)
    collision_departments = set()
    for conflict in conflicts:
        owner, overlap_owner = conflict["owner"], conflict["overlap_owner"]
        collision_departments.update((owner, overlap_owner))
        shared_score = max(max(scores.values(), default=0), 1)
        for department in (owner, overlap_owner):
            scores[department] = shared_score
            evidence.setdefault(department, [])
            for phrase in conflict["matched_phrases"]:
                for term in phrase.casefold().split():
                    if term in (text or "").casefold() and term not in evidence[department]:
                        evidence[department].append(term)

    if not scores:
        # Unknown complaints stay in manual review; never invent a confident owner.
        return {"top3": [], "evidence": {}, "confidence": 0.0, "abstained": True,
                "ownership_conflicts": conflicts}
        
    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0] not in collision_departments, x[0]))[:3]
    top3 = [{"dept": d, "score": round(s / total, 2)} for d, s in ranked]
    conf = top3[0]["score"]
    tie = len(ranked) > 1 and ranked[0][1] == ranked[1][1]
    abstained = conf < CONFIDENCE_THRESHOLD or tie or bool(conflicts)
    return {"top3": top3, "evidence": {d: evidence[d] for d, _ in ranked},
            "confidence": conf, "abstained": abstained, "ownership_conflicts": conflicts}
