"""Metrics for the frozen synthetic Engine 2 evaluation file."""
import json
from pathlib import Path
from .splitter import propose_split

DATASET = Path(__file__).resolve().parents[1] / "data" / "splitter_eval.json"


def evaluate(split="test"):
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    rows = [row for row in payload["rows"] if row["split"] == split]
    positives = [row for row in rows if row["expected_compound"]]
    negatives = [row for row in rows if not row["expected_compound"]]
    predictions = {row["id"]: propose_split(row["text"]) for row in rows}
    detected = [row for row in positives if predictions[row["id"]]["is_compound"]]
    false_splits = [row for row in negatives if predictions[row["id"]]["is_compound"]]
    exact_set = []
    per_part_correct = per_part_total = 0
    for row in positives:
        predicted = predictions[row["id"]]["parts"]
        expected_depts = [part["department"] for part in row["expected_parts"]]
        exact_set.append(bool(predicted) and {part.get("department") for part in predicted} == set(expected_depts))
        per_part_total += len(expected_depts)
        per_part_correct += sum(index < len(predicted) and predicted[index].get("department") == dept
                                for index, dept in enumerate(expected_depts))
    by_language = {}
    for language in sorted({row["language"] for row in rows}):
        lang_pos = [row for row in positives if row["language"] == language]
        lang_neg = [row for row in negatives if row["language"] == language]
        by_language[language] = {
            "compound_recall": f"{sum(predictions[row['id']]['is_compound'] for row in lang_pos)}/{len(lang_pos)}",
            "false_splits": f"{sum(predictions[row['id']]['is_compound'] for row in lang_neg)}/{len(lang_neg)}",
        }
    return {"label": payload["label"], "split": split, "n": len(rows),
            "compound_recall": f"{len(detected)}/{len(positives)}",
            "false_splits": f"{len(false_splits)}/{len(negatives)}",
            "exact_department_set_match": f"{sum(exact_set)}/{len(exact_set)}",
            "per_subissue_department_accuracy": f"{per_part_correct}/{per_part_total}",
            "by_language": by_language,
            "errors": [{"id": row["id"], "expected": row.get("expected_parts", row.get("expected_department")),
                        "predicted": predictions[row["id"]]} for row in rows
                       if (row["expected_compound"] != predictions[row["id"]]["is_compound"] or
                           (row["expected_compound"] and [part.get("department") for part in predictions[row["id"]]["parts"]] !=
                            [part["department"] for part in row["expected_parts"]]))]}
