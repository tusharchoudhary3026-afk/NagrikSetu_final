import json

import pandas as pd
import streamlit as st

from core.auth import verify_permission
from core.config import SIM_LABEL, feature_enabled
from core.corrections import load_verified_corrections
from core.retraining import run_offline_retraining_experiment
from core.safety import load_rubric, save_rubric_version
from core.ui import apply_demo_style, render_hero, require_permission
from core.verify_split import verify_frozen_test_split, verify_split_leakage

user = require_permission("can_view_model_eval")
if not feature_enabled("learning"):
    st.info("Offline learning experiments are disabled by configuration.")
    st.stop()
apply_demo_style()
render_hero("Technical review", "Model evaluation", "Run an offline candidate comparison; no model is saved or deployed by this page.")
st.warning(SIM_LABEL)
st.caption("TRY · Offline retraining quality is not established. Evaluation uses the same frozen hold-out set and never tunes on it.")

rubric = load_rubric()
st.subheader("Safety review rubric")
st.caption(f"Team-proposed version {rubric.get('version', 1)} · {rubric.get('status', 'TEAM-PROPOSED')} · flags request human attention only.")
st.json({"rules": rubric["rules"]})
if verify_permission(user.get("role"), "can_manage_corrections"):
    with st.expander("Create a new rubric version"):
        rubric_json = st.text_area("Edit rules as JSON", value=json.dumps(rubric["rules"],
                                                                           ensure_ascii=False, indent=2),
                                   height=260, key="safety-rubric-json")
        rubric_reason = st.text_input("Reason for this version (required)", key="safety-rubric-reason")
        if st.button("Save new team-proposed rubric version", key="save-safety-rubric"):
            try:
                rules = json.loads(rubric_json)
                version = save_rubric_version(user, rules, rubric_reason)
                st.success(f"Safety rubric version {version} saved to append-only history.")
                st.rerun()
            except (ValueError, PermissionError, json.JSONDecodeError) as exc:
                st.error(str(exc))
else:
    st.caption("Rubric configuration is read-only for this role.")

frozen = verify_frozen_test_split()
leakage = verify_split_leakage()
st.write({"Frozen test manifest": "verified" if frozen["passed"] else "FAILED",
          "Test rows": frozen.get("rows", 0), "Grouped split check": "passed" if leakage["passed"] else "FAILED",
          "Eligible verified corrections": len(load_verified_corrections())})
if not frozen["passed"] or not leakage["passed"]:
    st.error("Evaluation is blocked because the frozen split checks failed.")
    st.stop()

if st.button("Check readiness and run offline comparison", type="primary"):
    try:
        st.session_state["latest_offline_comparison"] = run_offline_retraining_experiment()
    except (ValueError, RuntimeError) as exc:
        st.error(f"The experiment could not run: {exc}")

result = st.session_state.get("latest_offline_comparison")
if not result:
    st.info("No experiment has been run in this session.")
elif result["status"] == "insufficient_verified_examples":
    st.info(f"Not ready for retraining: {result['verified_examples']} of "
            f"{result['minimum_required']} verified labels are available across "
            f"{len(result['departments'])} departments.")
    st.caption(f"Dataset version: {result['dataset_version']} · no model saved or deployed.")
else:
    st.caption(f"Dataset version: {result['dataset_version']} · {result['verified_examples']} verified examples · "
               f"{result['excluded_conflicting_labels']} conflicting labels excluded.")
    rows = []
    for key in ("baseline", "candidate"):
        metrics = result[key]
        rows.append({"Model": metrics["method"], "Correct / total":
                     f"{metrics['correct_overall']}/{metrics['total_single_owner']}",
                     "Answered": metrics["answered_count"], "Abstained": metrics["abstained_count"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    language_rows = []
    for key in ("baseline", "candidate"):
        for language, metrics in result[key]["by_language"].items():
            language_rows.append({"Model": key, "Language": language,
                                  "Correct / sample size": f"{metrics['correct']}/{metrics['n']}",
                                  "Answered": metrics["answered"], "Abstained": metrics["abstained"]})
    st.dataframe(pd.DataFrame(language_rows), hide_index=True, width="stretch")
    st.caption(f"Frozen test manifest: {result['test_manifest']} · candidate saved: {result['model_saved']} · "
               f"deployed: {result['deployed']}. No release control exists in this local prototype.")
