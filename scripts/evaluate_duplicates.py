"""Tune duplicate candidate threshold on dev pairs and report frozen test metrics."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from core.duplicates import compare_pair
from core.config import (DUP_TEXT_WEIGHT, DUP_ISSUE_WEIGHT, DUP_LOCATION_WEIGHT,
                         DUP_MIN_SCORE, DUP_TIME_WINDOW_DAYS)
from core.intake import extract_issue, extract_location, normalize

ROOT = Path(__file__).resolve().parents[1]
PAIR_FILE = ROOT / "data" / "duplicate_pairs.csv"
CONFIG_FILE = ROOT / "data" / "duplicate_threshold.json"
TEST_MANIFEST = ROOT / "data" / "duplicate_test_manifest.json"
LABELS = {"duplicate", "distinct"}


def _canonical_rows(df):
    rows = df.sort_values("pair_id").fillna("").to_dict("records")
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _sha256(payload):
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_pairs():
    df = pd.read_csv(PAIR_FILE, encoding="utf-8-sig", dtype=str).fillna("")
    required = {"pair_id", "group_id", "label", "text_a", "date_a", "text_b", "date_b", "split", "tag"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Duplicate-pair data is missing required columns: {', '.join(missing)}")
    if df.empty or set(df["label"]) - LABELS:
        raise ValueError("Duplicate-pair data must have rows labelled duplicate or distinct.")
    if set(df["split"]) - {"dev", "test"}:
        raise ValueError("Duplicate-pair rows may only use dev or test splits.")
    if df["group_id"].duplicated().any():
        raise ValueError("Each incident pair group must be unique in this labelled-pair file.")
    if df.groupby("group_id")["split"].nunique().gt(1).any():
        raise ValueError("A pair group appears across dev and test splits.")
    if not df["tag"].str.contains("SYNTHETIC", case=False).all():
        raise ValueError("Every labelled duplicate pair must be visibly tagged SYNTHETIC.")
    return df


def _freeze_or_verify_test(df, create=False):
    test = df[df["split"] == "test"]
    current = {"rows": len(test), "sha256": _sha256(_canonical_rows(test)),
               "groups": sorted(test["group_id"].unique().tolist()),
               "labels": sorted(test["label"].unique().tolist())}
    if create:
        TEST_MANIFEST.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        return current
    if not TEST_MANIFEST.exists():
        raise ValueError("Frozen duplicate test manifest is missing; create it only when initially freezing the test data.")
    manifest = json.loads(TEST_MANIFEST.read_text(encoding="utf-8"))
    if current != manifest:
        raise ValueError("Frozen duplicate test pairs differ from their manifest; refusing to score changed test data.")
    return manifest


def _features(row):
    text_a, text_b = row["text_a"], row["text_b"]
    return compare_pair(
        normalize(text_a), normalize(text_b),
        extract_location(text_a), extract_location(text_b),
        row["date_a"], row["date_b"], extract_issue(text_a), extract_issue(text_b),
    )


def _tune_dev(dev, min_precision=0.8):
    scored = [(row, _features(row)) for row in dev.to_dict("records")]
    candidates = sorted({0.0, 1.0, *(signal["score"] for _, signal in scored)})
    metrics = []
    for threshold in candidates:
        tp = sum(row["label"] == "duplicate" and signal["score"] >= threshold and not signal["hard_conflict"]
                 for row, signal in scored)
        fp = sum(row["label"] == "distinct" and signal["score"] >= threshold and not signal["hard_conflict"]
                 for row, signal in scored)
        positives = sum(row["label"] == "duplicate" for row, _ in scored)
        predicted = tp + fp
        precision = tp / predicted if predicted else 0.0
        recall = tp / positives if positives else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        metrics.append({"threshold": threshold, "true_positive": tp, "false_positive": fp,
                        "precision": precision, "recall": recall, "f1": f1})
    safe = [row for row in metrics if row["precision"] >= min_precision and row["true_positive"] > 0]
    choices = safe or [row for row in metrics if row["true_positive"] > 0]
    if not choices:
        # If all non-zero candidate cutoffs miss the preferred precision floor,
        # retain the configured conservative placeholder and abstain from a label.
        return {"threshold": DUP_MIN_SCORE, "true_positive": 0, "false_positive": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0}, scored
    if not safe:
        # Do not trade away the stated precision floor just to maximize a weak synthetic F1.
        conservative = [row for row in metrics if row["threshold"] == 1.0]
        if conservative:
            return conservative[0], scored
    # Prefer F1, then precision, then a stricter threshold when tied.
    best = max(choices, key=lambda row: (row["f1"], row["precision"], row["threshold"]))
    return best, scored


def tune_duplicate_threshold():
    """Tune exclusively on the group-separated dev pairs and freeze the chosen config."""
    df = load_pairs()
    dev = df[df["split"] == "dev"]
    best, _ = _tune_dev(dev)
    config = {
        "schema_version": 1,
        "threshold": round(best["threshold"], 4),
        "time_window_days": DUP_TIME_WINDOW_DAYS,
        "weights": {"text": DUP_TEXT_WEIGHT, "issue": DUP_ISSUE_WEIGHT,
                    "location": DUP_LOCATION_WEIGHT},
        "tuned_on_split": "dev",
        "tuning_metric": "F1 with a preferred precision floor of 0.80",
        "dev_precision_floor": 0.80,
        "dev_precision_floor_met": best["precision"] >= 0.80,
        "dev_tuning_note": ("preferred precision floor met" if best["precision"] >= 0.80 and best["true_positive"] > 0 else
                            "no non-zero-prediction dev threshold met the preferred precision floor; conservative configured threshold retained"),
        "dev_pairs": len(dev),
        "dev_true_positive": best["true_positive"],
        "dev_false_positive": best["false_positive"],
        "dev_precision": round(best["precision"], 4),
        "dev_recall": round(best["recall"], 4),
        "dev_f1": round(best["f1"], 4),
        "dev_sha256": _sha256(_canonical_rows(dev)),
        "label_limit": "Synthetic AI-authored labels; not independently human-labelled ground truth.",
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def _binary_metrics(rows, threshold):
    scored = [(row, _features(row)) for row in rows.to_dict("records")]
    predicted = [signal["score"] >= threshold and not signal["hard_conflict"] for _, signal in scored]
    actual = [row["label"] == "duplicate" for row, _ in scored]
    tp = sum(p and a for p, a in zip(predicted, actual))
    fp = sum(p and not a for p, a in zip(predicted, actual))
    fn = sum(not p and a for p, a in zip(predicted, actual))
    tn = sum(not p and not a for p, a in zip(predicted, actual))
    pden, rden = tp + fp, tp + fn
    return {
        "pairs": len(rows), "true_positive": tp, "precision_denominator": pden,
        "precision": round(tp / pden, 4) if pden else None,
        "recall_denominator": rden, "recall": round(tp / rden, 4) if rden else None,
        "false_positives": fp, "negative_denominator": fp + tn,
        "true_negatives": tn, "false_negatives": fn,
        "accuracy_numerator": tp + tn, "accuracy_denominator": len(rows),
        "accuracy": round((tp + tn) / len(rows), 4) if len(rows) else None,
        "candidate_scores": [{"pair_id": row["pair_id"], "label": row["label"],
                              "score": signal["score"], "hard_conflict": signal["hard_conflict"],
                              "conflicts": signal["conflicts"]}
                             for (row, signal) in scored],
    }


def evaluate_duplicate_pairs():
    df = load_pairs()
    manifest = _freeze_or_verify_test(df)
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if config.get("tuned_on_split") != "dev":
        raise ValueError("Duplicate threshold must be tuned on the dev split only.")
    if config.get("dev_sha256") != _sha256(_canonical_rows(df[df["split"] == "dev"])):
        raise ValueError("Dev pairs have changed since threshold tuning; re-tune using dev only and record the change.")
    dev, test = df[df["split"] == "dev"], df[df["split"] == "test"]
    threshold = float(config["threshold"])
    return {
        "status_label": "SIMULATED / SYNTHETIC; AI-authored pair labels, not independent human ground truth",
        "dataset_pairs": len(df), "dev_pairs": len(dev), "test_pairs": len(test),
        "groups": int(df["group_id"].nunique()), "group_leakage": 0,
        "threshold": threshold, "threshold_source": "dev",
        "test_manifest_sha256": manifest["sha256"],
        "dev": _binary_metrics(dev, threshold), "test": _binary_metrics(test, threshold),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tune", action="store_true", help="Tune and freeze threshold from dev pairs only.")
    parser.add_argument("--freeze-test-manifest", action="store_true",
                        help="Create the immutable hash manifest for the current test pair split.")
    args = parser.parse_args()
    if args.freeze_test_manifest:
        print(json.dumps(_freeze_or_verify_test(load_pairs(), create=True), indent=2))
        return
    if args.tune:
        print(json.dumps(tune_duplicate_threshold(), indent=2))
        return
    print(json.dumps(evaluate_duplicate_pairs(), indent=2))


if __name__ == "__main__":
    main()
