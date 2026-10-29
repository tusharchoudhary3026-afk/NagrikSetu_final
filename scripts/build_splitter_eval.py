"""Build the frozen, synthetic Engine 2 evaluation set (AI-authored labels)."""
import json
from pathlib import Path


LANGUAGES = {
    "english": {"join": " and ", "Roads Dept": "Pothole on the road",
                "Electricity Dept": "Streetlight is not working", "Sanitation Dept": "Garbage is not collected",
                "Drainage Dept": "The drain is blocked", "Water Supply Dept": "Water supply is not working"},
    "hinglish": {"join": " aur ", "Roads Dept": "Sadak par gadda hai",
                 "Electricity Dept": "Streetlight band hai", "Sanitation Dept": "Kachra nahi utha",
                 "Drainage Dept": "Nali block hai", "Water Supply Dept": "Water supply nahi aa rahi"},
    "hindi": {"join": " और ", "Roads Dept": "सड़क पर गड्ढा है", "Electricity Dept": "स्ट्रीटलाइट बंद है",
              "Sanitation Dept": "कचरा नहीं उठा", "Drainage Dept": "नाली बंद है",
              "Water Supply Dept": "पानी की सप्लाई नहीं आ रही"},
}
PAIRS = [
    ("Roads Dept", "Electricity Dept"), ("Electricity Dept", "Sanitation Dept"),
    ("Roads Dept", "Drainage Dept", "Sanitation Dept"), ("Water Supply Dept", "Roads Dept"),
    ("Sanitation Dept", "Electricity Dept"), ("Roads Dept", "Water Supply Dept"),
    ("Drainage Dept", "Electricity Dept"), ("Sanitation Dept", "Water Supply Dept"),
    ("Water Supply Dept", "Drainage Dept"), ("Electricity Dept", "Drainage Dept"),
    ("Roads Dept", "Sanitation Dept"), ("Water Supply Dept", "Electricity Dept"),
    ("Drainage Dept", "Roads Dept"), ("Sanitation Dept", "Drainage Dept"),
]
NEGATIVES = [
    ("Roads Dept", "same road, one broken road incident"),
    ("Electricity Dept", "one streetlight outage described twice"),
    ("Sanitation Dept", "same garbage collection failure described twice"),
    ("Drainage Dept", "one blocked drain described twice"),
    ("Water Supply Dept", "one water supply outage described twice"),
    ("Roads Dept", "one pothole with added safety context"),
    ("Drainage Dept", "one waterlogging issue on a road"),
    ("Roads Dept", "one footpath issue with added context"),
]


def build():
    rows = []
    for language, phrases in LANGUAGES.items():
        for index, depts in enumerate(PAIRS, 1):
            parts = [phrases[dept] for dept in depts]
            rows.append({"id": f"C-{language}-{index:02}", "family": f"compound-{index:02}",
                         "split": "test" if index in {5, 10, 12, 14} else "dev",
                         "language": language, "text": phrases["join"].join(parts),
                         "expected_compound": True,
                         "expected_parts": [{"text": text, "department": dept}
                                            for text, dept in zip(parts, depts)],
                         "labeller": "AI_AUTHORED_SYNTHETIC", "tag": "SYNTHETIC"})
        for index, (dept, note) in enumerate(NEGATIVES, 1):
            one = phrases[dept]
            # Second clause repeats the same owner; context words do not assert a new issue.
            if language == "english":
                second = {"Roads Dept": "the road is badly broken", "Electricity Dept": "the light remains off",
                          "Sanitation Dept": "the waste is still there", "Drainage Dept": "water remains in the drain",
                          "Water Supply Dept": "there is still no water"}[dept]
                text = one + phrases["join"] + second
            elif language == "hinglish":
                second = {"Roads Dept": "sadak bhi tooti hai", "Electricity Dept": "light abhi bhi band hai",
                          "Sanitation Dept": "kachra abhi bhi pada hai", "Drainage Dept": "nali ka paani ruka hai",
                          "Water Supply Dept": "nal mein paani nahi hai"}[dept]
                text = one + phrases["join"] + second
            else:
                second = {"Roads Dept": "सड़क भी टूटी है", "Electricity Dept": "लाइट अभी भी बंद है",
                          "Sanitation Dept": "कचरा अभी भी पड़ा है", "Drainage Dept": "नाली का पानी रुका है",
                          "Water Supply Dept": "नल में पानी नहीं है"}[dept]
                text = one + phrases["join"] + second
            rows.append({"id": f"N-{language}-{index:02}", "family": f"negative-{index:02}",
                         "split": "test" if index in {6, 7, 8} else "dev", "language": language,
                         "text": text, "expected_compound": False, "expected_department": dept,
                         "negative_reason": note, "labeller": "AI_AUTHORED_SYNTHETIC", "tag": "SYNTHETIC"})
    return rows


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "data" / "splitter_eval.json"
    target.write_text(json.dumps({"label": "SYNTHETIC; AI-authored; not independently human-labelled",
                                  "split_policy": "family-grouped; test family ids frozen in builder",
                                  "rows": build()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(build())} SYNTHETIC rows to {target}")
