import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import streamlit as st
import pandas as pd
from scripts.evaluate import run, summary
from core.config import SIM_LABEL, feature_enabled
from core.engine8_harness import evaluate_component_3_and_5
from core.analytics import (detect_emerging_hotspots, get_ownership_gap_report,
                            get_reassignment_breakdown, get_transfer_pattern_summary)
from scripts.evaluate_duplicates import evaluate_duplicate_pairs
from core.ui import require_permission
from core.safety import evaluate_safety_rubric
from core.splitter_evaluation import evaluate as evaluate_splitter

user = require_permission("can_audit")
st.title("4. Evaluation")
st.error(SIM_LABEL)
st.caption("Baseline on the frozen test split. Numerator/denominator reported; small n means preliminary.")
st.subheader("Safety rubric sanity checks")
safety = evaluate_safety_rubric()
st.write({"label": safety["status_label"], "total": safety["total"],
          "recall": f"{safety['true_positive']}/{safety['positive_denominator']}",
          "false positives": f"{safety['false_positive']}/{safety['negative_denominator']}",
          "by language": safety["by_language"]})
st.caption(safety["limitation"])

st.subheader("Compound complaint splitter · SYNTHETIC")
st.caption("AI-authored examples; not independently human-labelled. Test families are grouped and held out in the dataset manifest.")
if st.button("Run frozen splitter test evaluation"):
    splitter_result = evaluate_splitter("test")
    st.write({key: value for key, value in splitter_result.items() if key != "errors"})
    if splitter_result["errors"]:
        st.dataframe(splitter_result["errors"], width="stretch", hide_index=True)

st.caption("The held-out test split is used once. Model fitting uses train; threshold selection uses validation.")
if st.button("Run frozen test evaluation"):
    out = run("test")
    st.code(summary(out))
    st.subheader("Failures and abstentions")
    st.dataframe(out[out.outcome != "correct"])
    comparison = evaluate_component_3_and_5("test")
    st.subheader("Baseline vs TF-IDF (same frozen test rows)")
    st.dataframe([{key: value for key, value in summary.items() if key != "by_language"}
                  for summary in (comparison["baseline"], comparison["tfidf"])], width="stretch")
    for model in ("baseline", "tfidf"):
        st.markdown(f"**{model.upper()} results by language (correct / sample size)**")
        st.dataframe([{"language": language, **metrics}
                      for language, metrics in comparison[model]["by_language"].items()],
                     width="stretch", hide_index=True)
    curve = pd.DataFrame(comparison["component_4_curve"]).set_index("threshold")
    st.markdown("**Validation-only coverage / accuracy threshold curve**")
    st.line_chart(curve[["accuracy", "coverage"]])
    if comparison["threshold_used"] is None:
        st.warning("Validation did not meet the minimum coverage target. TF-IDF is not cleared to answer; its cases remain for human review.")
    else:
        st.caption(f"Validation-selected TF-IDF threshold: {comparison['threshold_used']}. "
                   "Results are preliminary on a small synthetic dataset; no fairness or real-world performance claim is made.")
    st.subheader("Reassignment reasons")
    st.write(get_reassignment_breakdown(user))
    st.caption("Counts describe recorded transfers; they do not establish avoidability or fault.")
    st.subheader("Ownership gap report")
    st.dataframe(get_ownership_gap_report(user), width="stretch")
    st.subheader("Potential concentration for staff review")
    st.dataframe(detect_emerging_hotspots(actor_user=user), width="stretch")

if feature_enabled("learning") and user.get("role") in {"reviewer", "auditor", "government"}:
    st.divider()
    st.page_link("pages/15_Model_Evaluation.py", label="Open offline model evaluation")

st.divider()
transfer_tab, duplicate_tab = st.tabs(["Transfer patterns across cases", "Duplicate candidates"])
with transfer_tab:
    st.subheader("Transfer patterns across cases")
    patterns = get_transfer_pattern_summary(user)
    st.warning(patterns["status_label"])
    st.caption("Flags summarize recorded transfer paths for human review. They do not assign fault. "
               "Denominators are cases with at least one transfer in your authorized scope.")
    st.dataframe([
        {"pattern": "A → B → A", **patterns["A_to_B_to_A"]},
        {"pattern": "Repeat reassignment", **patterns["repeat_reassignment"]},
    ], width="stretch", hide_index=True)
    st.caption(f"Cases in scope: {patterns['total_cases']} · Cases with transfers: {patterns['cases_with_transfers']}")

with duplicate_tab:
    st.subheader("Duplicate candidate evaluation · synthetic")
    st.warning(SIM_LABEL)
    st.caption("Small AI-authored/AI-reviewed pairs; not independently labelled ground truth. "
               "The threshold is selected on development data only. Similarity is not proof, and "
               "Hindi-English cross-language matches may be missed.")
    try:
        duplicate_metrics = evaluate_duplicate_pairs()
        st.caption(f"Threshold: {duplicate_metrics['threshold']} · source: {duplicate_metrics['threshold_source']} · "
                   f"test pairs: {duplicate_metrics['test_pairs']} · test manifest verified")
        st.dataframe([
            {"split": split, "precision": f"{values['true_positive']}/{values['precision_denominator']}",
             "recall": f"{values['true_positive']}/{values['recall_denominator']}",
             "false positives": f"{values['false_positives']}/{values['negative_denominator']}",
             "true negatives": values["true_negatives"], "false negatives": values["false_negatives"],
             "accuracy": f"{values['accuracy_numerator']}/{values['accuracy_denominator']}"}
            for split, values in (("dev", duplicate_metrics["dev"]), ("test", duplicate_metrics["test"]))
        ], width="stretch", hide_index=True)
    except (ValueError, OSError) as exc:
        st.error(f"Duplicate evaluation is unavailable: {exc}")
