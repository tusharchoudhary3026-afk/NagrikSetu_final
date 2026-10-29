# Compound splitter investigation and fix

Date: 2026-10-29. Dataset and all measurements below are **SYNTHETIC**.

## Screenshot and root cause

The screenshot shows a single-value extraction and a whole-complaint router result. The exact parent string is classified as `Electricity` by intake's fixed category-priority matcher, while whole-text routing ranks `Roads Dept` before `Electricity Dept`. Those results describe the complete text; neither is a clause-by-clause split. The public preview rendered only a generic “may contain more than one issue” note. Its submit path did not persist the proposal, so staff had no split proposal to review. Separately, the custom voice component sent `apiVersion` inside `value`, but Streamlit reads it at the top level; that caused the visible `undefined` API version error.

Reproduction command:

```powershell
$env:PYTHONPATH = 'D:\IIITM PS;D:\IIITM PS\.venv\Lib\site-packages'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\victus\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\repro_compound_split.py
```

Observed before the UI/persistence fix: parent category `Electricity`; parent route order `Roads Dept`, `Electricity Dept`. The splitter's real clause analysis returns:

```text
1. offsets=(0,18) text='road has a pothole' -> Roads Dept; evidence=['pothole', 'road']
2. offsets=(24,69) text='the streetlight is also not working in ward 3' -> Electricity Dept; evidence=['streetlight']
is_compound=True; human confirmation required
```

The attached screenshot's “ward 3” remains only in the original phrase. The child inherits the geography selected on the form; the splitter does not extract the ward into a new location field.

## Pipeline map

`pages/10_Public_Portal.py` validates the citizen form and calls intake `process`, baseline router `route`, ownership/safety checks, and `propose_split`. On confirmation of the citizen's extracted details, `insert_public_submission` writes the immutable parent, parent suggestion and `split_proposals` row plus `split_proposed` event in one transaction. The staff-assisted intake now uses the same writer. It returns the parent case ID only after commit. `pages/2_Review.py` reads that proposal under the existing role/geography scope; a reviewer may confirm or edit it with a reason. `confirm_split` inserts child complaint rows with the same case ID, parent ID, source offsets, child suggestion/evidence and append-only events. `merge_back` requires a reason, deactivates child views, preserves child rows/history and writes `split_merged_back`. The citizen's “My complaints” page reads the proposal and any confirmed children.

## Hypotheses

| Hypothesis | Result | Evidence |
|---|---|---|
| A. Segmentation covers requested English/Hindi/Hinglish connectors, punctuation, lists, offsets and minimum filters | PARTIAL | Acceptance and regression cases cover `and`, comma, semicolon/list, danda, `aur`, `तथा`, `और`, `&`, and `saath mein`; offsets point to exact source snippets. “also” alone does not split. `bhi` is treated as clause-internal emphasis, not a boundary. The rule splitter still misses some phrasing (see Hindi evaluation miss). |
| B. Issue extraction and routing run per clause | PASS | Each proposal part stores its own issue extraction, route, top-3/evidence, confidence/abstain and overlap results. |
| C. Single category overwrote/collapsed multi-issue result | PASS (cause confirmed) | Whole-text intake has one `issue` field and picks `Electricity` by fixed matcher priority. The public preview used that parent category despite two router candidates. Compound preview now shows per-part categories. |
| D. Compound decision requires two independently grounded owners | PASS | The splitter requires issue evidence per part and at least two non-abstained suggested departments; same-owner/unclear examples remain intact. |
| E. Proposal persisted on public submit | PASS (bug fixed) | Parent, suggestion, proposal row and `split_proposed` ledger event are written in one transaction; regression checks no child exists before review. |
| F. Staff sees and can confirm/edit/merge the proposal | PASS for tested flow | Reviewer AppTest confirms and creates linked children; merge-back reason and append-only events are tested. Split edits are constrained to source text spans and evidence-backed department candidates. |
| G. Proposal survives Streamlit reruns and citizen edit step | PASS for tested flow | Citizen AppTest submits the preview and reads its persisted proposal; staff confirmation uses the database row. |
| H. Existing visibility/area authorization is retained | PASS for regression suite | Confirmation still invokes the existing authorization check and the normal staff list scoping. No geography gate was removed. |

## Changes

- `core/splitter.py`: Unicode-aware clause segmentation with source offsets, per-clause evidence/routing, conservative grouping and overlap handling; reasoned edit/merge ledger events; reviewer confirmation validates exact source spans.
- `pages/10_Public_Portal.py`: displays each proposed clause, category, routing evidence and offsets; stores the proposal only after citizen confirmation; exposes proposal/child status in “My complaints”.
- `core/db.py`, `db/schema.sql`: additive `source_start` / `source_end` columns and `split_proposals` storage; atomic `split_proposed` event. SQLite trigger rejects split edit/merge events without reasons. Parent original text remains untouched.
- `pages/2_Review.py`, `core/portal.py`, `core/staff_dashboard.py`, `core/staff_dashboard_ui.py`, `pages/6_Inbox.py`, `pages/7_Replay.py`: reviewer actions, citizen/staff visibility and split states in queue/history.
- `components/speech_input/index.html`: sends Streamlit's `apiVersion` at the top level. Voice remains optional; typed text is still available.
- `data/splitter_eval.json`, `scripts/build_splitter_eval.py`, `core/splitter_evaluation.py`, `pages/4_Evaluation.py`: 66 AI-authored synthetic examples with grouped dev/test families and frozen test-family selection; the evaluation page displays test metrics and errors.
- `tests/test_compound_splitter_regression.py`: exact reported input, multilingual/list cases, negative cases, citizen/staff AppTest, persistence/confirmation/merge, migration, speech protocol and synthetic dataset checks.

## Synthetic evaluation

The evaluation data are AI-authored and AI-labelled; they are not independently reviewed by municipal staff. The 66 rows comprise 42 compound and 24 negative examples across English, Hindi and Hinglish. Related language variants are grouped by family, so a family cannot cross dev/test. Test families were frozen after the implementation had already been tuned against the prompt's acceptance examples. Therefore these are exploratory regression metrics, not an unbiased estimate of real-world performance. No code tuning was done after evaluating this frozen test file.

Frozen synthetic test results (`n=21`: 12 compound, 9 negative):

| Metric | Result |
|---|---:|
| Compound detection recall | 11/12 |
| False splits on negatives | 0/9 |
| Exact department-set match | 11/12 |
| Per-sub-issue department accuracy | 22/24 |
| English compound recall / negative false splits | 4/4 / 0/3 |
| Hindi compound recall / negative false splits | 3/4 / 0/3 |
| Hinglish compound recall / negative false splits | 4/4 / 0/3 |

Missed test row: Hindi water-supply plus streetlight. This exposes a real keyword coverage limitation; it remains visible as an unsplit complaint for staff review. The current scorer reports the department metrics above; calibrated confidence/coverage is not claimed for the splitter.

## Verification

- `python -m pytest tests -q`: **184 passed** on the final run, including dataset-integrity and connector regressions; the output is recorded in `tests/results.md`.
- `python -m scripts.run_acceptance`: the test suite and AT-01 through AT-06 passed; it also records AT-07 through AT-08 in the full suite.
- UI AppTest exercises citizen preview → persisted parent/proposal → reviewer confirmation → linked children. It is a Streamlit AppTest, not a real external browser session.
- `python -m pytest -q` at repository root could not collect because `_pytest_tmp_select_area` is inaccessible and `scripts/test_ledger_lock.py` is a manual SQLite script whose test-only connection lacks the required `nyaya_ledger_hash` function. Running the supported `tests/` suite succeeds.
- The checked-in `.venv` launcher points to a missing local Python 3.12 installation in this environment. Verification used the bundled Python executable with the existing `.venv/Lib/site-packages` added to `PYTHONPATH`.

## Limits

The splitter remains deterministic and keyword-based; unusual clause boundaries, Hindi idioms, or unrecognized synonyms can be missed. A missed split does not delete or hide the complaint; it stays intact for staff. Added/edited snippets must be exact spans from the original text, and suggested department adjustments are limited to evidence-backed route candidates. No real-browser screenshot was captured in this run; the UI-level path was verified with Streamlit AppTest.
