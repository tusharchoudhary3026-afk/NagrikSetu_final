# NagrikSetu / NyayaFlow implementation audit

Audit date: 2026-10-28. Source: the user's updated feature-scope PDF and final-round workflow PDF. “WORKS” means an end-to-end test or command exercised the behavior; “PARTIAL” means some behavior is tested but a stated capability or evidence is missing. Synthetic labels and outputs are not real-world performance evidence. The 23% reassignment figure in the case framing is not treated as a misrouting rate.

## Item-by-item matrix

| Item | Requirement | Code location | Status | Evidence | Remaining work |
|---|---|---|---|---|---|
| 1 | English, Hindi and Hinglish intake | `core/intake.py`, `pages/1_Intake.py` | WORKS | AT-06 and intake tests | Validate on real citizen phrasing |
| 2 | Preserve submitted original text | `core/db.py`, `db/schema.sql` | WORKS | Intake, split and ledger tests | None for prototype |
| 3 | Detect language/style, including uncertainty | `core/intake.py` | PARTIAL | AT-06 and intake tests | Word-list detector is not robustly calibrated |
| 4 | Separate normalized/transliterated text | `core/intake.py`, `db/schema.sql` | PARTIAL | Intake tests | Roman/Devanagari normalization coverage is limited |
| 5 | Extract issue with text evidence | `core/intake.py`, intake pages | WORKS | `test_intake_issue_extraction_exposes_matched_words` | Expand validation set |
| 6 | Extract ward/location without inventing it | `core/intake.py` | PARTIAL | location regression tests | Proximity/geocoding is not implemented |
| 7 | Extract requested action, leave absent action unspecified | `core/intake.py` | WORKS | positive and negative action tests | Expand vocabulary beyond current rules |
| 8 | Detect missing required fields | `core/intake.py` | WORKS | vague intake and acceptance tests | Rules need field review with municipal staff |
| 9 | Ask a targeted clarification | `core/intake.py`, intake pages | WORKS | AT-02 | Validate question usefulness with residents |
| 10 | Store intake channel | `core/portal.py`, `core/db.py` | WORKS | Portal/workflow tests | Social/phone integrations are not connected |
| 11 | Staff-assisted intake | `pages/1_Intake.py` | WORKS | all-pages AppTest | Operational workflow needs pilot |
| 12 | Speech transcription | `core/speech.py`, `core/speech_component.py` | MOCK | all-pages AppTest; transcript validation test | Actual microphone/audio transcription and language accuracy unverified |
| 13 | Private photo attachment, authorized access | `core/attachment.py`, `pages/2_Review.py` | WORKS | encryption, role-access and upload validation tests | Deployment key management and operational access review |
| 14 | Citizen confirms extracted fields | `pages/10_Public_Portal.py` | PARTIAL | portal page smoke test | Explicit confirm/edit step before submission needs completion |
| 15 | Detect potential multi-issue complaints | `core/splitter.py` | WORKS | AT-07; 66-row SYNTHETIC evaluation fixture | Rule-based segmentation still misses some Hindi phrasing; see splitter report |
| 16 | Extract grounded candidate sub-issues | `core/splitter.py`, `pages/10_Public_Portal.py` | WORKS | Citizen AppTest shows per-clause categories, evidence and offsets | Edit remains constrained to exact spans of the parent text |
| 17 | Link children under parent case | `core/splitter.py`, `db/schema.sql` | WORKS | split confirmation/merge test | None for prototype |
| 18 | Suggest lead department | `core/splitter.py` | PARTIAL | split and abstention tests | Needs an explicit reviewed lead-owner rule |
| 19 | Human confirm/edit/merge-back | `core/splitter.py`, `pages/2_Review.py` | WORKS | Reviewer AppTest confirms; reasoned edit/merge paths use ledger events | Department adjustment is limited to evidence-backed candidates |
| 20 | Keep unclear complaints intact and flagged | `core/splitter.py` | WORKS | unclear/abstention regression tests | None for prototype |
| 21 | Keyword baseline router | `core/router_baseline.py` | WORKS | `scripts.evaluate_full` | Weak measured accuracy; see TEST_REPORT |
| 22 | TF-IDF/logistic regression router | `core/router_ml.py` | PARTIAL | `scripts.evaluate_full`, `scripts.diagnose_tfidf` | Validation selects no safe threshold; test answers 0/21 |
| 23 | Multilingual embedding router | — | ROADMAP | No implementation found | Add only after licensed model/data and evaluation plan |
| 24 | LLM extraction comparison | — | MOCK | No live LLM path | Not required for deterministic core routing |
| 25 | Top-3 suggestions with evidence | `core/router_baseline.py`, review pages | WORKS | AppTest and evaluation | Improve evidence coverage |
| 26 | Confidence score | `core/router_baseline.py`, `core/router_ml.py` | PARTIAL | evaluation output | Scores are uncalibrated; must not be read as probabilities |
| 27 | Abstain and ask on low confidence | `core/suggestions.py`, pages | WORKS | abstention regression tests | None for enforced abstention behavior |
| 28 | Manual-review queue for uncertain cases | `pages/11_Request_Review_Queue.py`, `pages/6_Inbox.py` | PARTIAL | page AppTest | Queue does not yet filter all uncertain/abstained cases as a dedicated inbox |
| 29 | No automatic close/reject/finalize | `core/reviews.py`, safety tests | WORKS | `test_automatic_final_actions_are_rejected_without_mutation` | Continue to guard any new action paths |
| 30 | Responsibility table, target 10–15 issue types | `data/responsibility_table.csv`, `core/ownership.py` | PARTIAL | evaluator: 8/8 rows unverified | Expand to target size and verify with city sources |
| 31 | Flag overlapping ownership by issue/cues | `core/ownership.py` | PARTIAL | AT-08; overlap tests | Measured overlap recall is low; improve rule coverage |
| 32 | Show owner rule and source | `core/ownership.py`, review and staff dashboard | PARTIAL | AT-08 and explanation test | ASSUMPTION rows now say “unverified rule”; sources still need authoritative verification |
| 33 | Detect A→B→A transfers | `core/analytics.py` | WORKS | `test_E6_bounce_pattern` | Add checked-in transfer-history fixture for repeatable demo |
| 34 | Flag repeat reassignments without blame | `core/analytics.py` | WORKS | analytics tests | More realistic synthetic histories needed |
| 35 | Lead-department proposal for overlap | `core/ownership.py` | MOCK | no production assignment flow | Keep as human-reviewed suggestion |
| 36 | Cross-department shared review thread | — | MOCK | no shared-thread service | Out of current prototype |
| 37 | Duplicate text similarity | `core/duplicates.py` | PARTIAL | duplicate evaluator and service tests | Devanagari-safe TF-IDF word 1–2 grams; cross-language matches can be missed and metrics remain synthetic |
| 38 | Location proximity match | `core/duplicates.py` | PARTIAL | duplicate tests | String-based same/nearby/different/unknown; no geographic proximity |
| 39 | Configurable time-window match | `core/duplicates.py`, `data/duplicate_threshold.json` | WORKS | duplicate evaluation and service tests | Validate window with real operational data |
| 40 | Candidate evidence panel | `core/duplicates.py`, `pages/2_Review.py` | WORKS | duplicate UI code and page smoke test | None for prototype |
| 41 | Same-place/different-incident safeguards | `core/duplicates.py` | PARTIAL | numbered-asset/date/issue tests | Numeric and issue conflict heuristics need broader validation |
| 42 | Human confirm, reject, or unlink | `core/duplicates.py`, `pages/2_Review.py`, `pages/9_Duplicates.py` | WORKS | reason, event, and staff-page tests | Appeal workflow not separate; link history remains append-only |
| 43 | Incident cluster map | — | MOCK | no map integration | Not required for review workflow |
| 44 | Automatic duplicate merging | — | REMOVED | service only links after explicit human decision | Explicitly prohibited by source scope |
| 37a | Normalize comparable values separately from originals | `core/intake.py`, `core/duplicates.py` | WORKS | original-preservation and duplicate tests | None for prototype |
| 37b | Weighted TF-IDF/issue/location ranking with visible components | `core/duplicates.py`, `core/config.py` | WORKS | duplicate score tests | Placeholder score is not a calibrated probability |
| 37c | Shared terms, location, time, missing fields, conflicts | `core/duplicates.py`, `pages/2_Review.py` | WORKS | candidate evidence and page smoke test | None for prototype |
| 37d | likely same / related / distinct / insufficient evidence | `core/duplicates.py` | WORKS | outcome-label test | Labels are decision support only |
| 37e | Different date/asset/issue safeguards | `core/duplicates.py` | PARTIAL | synthetic hard-case tests | False-positive behavior remains weak on pair test set |
| 37f | Human decision and structured reason code | `core/duplicates.py`, `pages/2_Review.py` | WORKS | reason-code and acceptance tests | “Need more information” is stored as a review decision, not a separate request flow |
| 37g | Canonical incident link without deleting originals | `db/schema.sql`, `core/duplicates.py` | WORKS | canonical-group and append-only tests | None for prototype |
| 37h | Unlink/reopen with append-only audit | `core/duplicates.py`, ledger | WORKS | link/unlink history test | Appeal UI remains absent |
| 37i | No-match/low-confidence abstention | `core/duplicates.py` | PARTIAL | candidate service tests | Missing fields are exposed, but no targeted date/location request is generated |
| 37j | Configurable threshold tuned only on dev and frozen test | `scripts/evaluate_duplicates.py`, config/manifest | WORKS | evaluator verifies split hash and dev hash | Threshold has low recall; see report |
| 45 | Editable team-proposed safety rubric | `data/safety_rules.json`, `core/safety.py` | WORKS | rubric tests | Official review not obtained |
| 46 | Send safety flags to human review | `core/safety.py`, review queue | WORKS | AT-05 and queue tests | Queue operations need pilot validation |
| 47 | Versioned rubric configuration | `data/safety_rules.json`, `core/safety.py`, `safety_rubric_versions` | WORKS | rubric versioning tests | Team-proposed only; no official review |
| 48 | Calm, low-volume hazard cases | `data/safety_test_cases.csv` | WORKS | safety test set and safety tests | 12 synthetic cases are not independent labels |
| 49 | Sentiment urgency scoring | — | REMOVED | no sentiment score path | Explicitly excluded for safety |
| 50 | Accept/edit/clarify/escalate review actions | `core/reviews.py`, review UI | WORKS | workflow service tests | None for prototype |
| 51 | Mandatory override reason | `core/reviews.py` | WORKS | reviewer override tests | None for prototype |
| 52 | Structured transfer reason code | `core/reviews.py`, ledger | WORKS | service and workflow tests | Keep vocabulary aligned with city process |
| 53 | Log suggestion/evidence/decision/time | `core/ledger.py`, `db/schema.sql` | WORKS | ledger/replay tests | Evidence payload consistency can be expanded |
| 54 | Append-only decision history | `db/schema.sql` | WORKS | DB trigger regression test | Hash-chain integrity is not provided |
| 55 | Staff route challenge action | `core/challenges.py`, pages | WORKS | challenge service tests | None for prototype |
| 56 | Reviewer resolution logged | `core/challenges.py` | WORKS | challenge resolution test | None for prototype |
| 57 | Citizen-side challenge | `core/portal.py`, `pages/10_Public_Portal.py` | WORKS | portal/challenge acceptance tests | No live citizen pilot |
| 58 | Save correction labels with provenance | `core/corrections.py`, schema | WORKS | corrections and provenance tests | Labels need independent verification before training |
| 59 | Offline retraining on verified corrections | `core/retraining.py`, scripts | PARTIAL | no-verified-example test | No eligible verified set; no before/after model run |
| 60 | Hash-chained tamper-evident ledger | — | ROADMAP | no hash-chain implementation | Current DB triggers only prevent ordinary UPDATE/DELETE |
| 61 | Frozen grouped test split | `core/verify_split.py`, manifest | WORKS | frozen manifest regression test | None for current dataset |
| 62 | Baseline and TF-IDF on same test rows | `scripts/evaluate_full.py` | WORKS | evaluation command | TF-IDF abstains on all rows |
| 63 | Top-1 accuracy with denominators | `scripts/evaluate_full.py` | WORKS | evaluation output and report | Small synthetic test sample |
| 64 | Coverage/accuracy trade-off on validation | `core/router_ml.py`, evaluation UI | PARTIAL | validation threshold curve | No threshold meets minimum coverage; plot needs operator review |
| 65 | Language metrics and sample sizes | `scripts/evaluate_full.py` | WORKS | per-language `k/n` and Wilson intervals | Only n=7 per language in single-owner test subset |
| 66 | Hard-case suite | tests/evaluation scripts | PARTIAL | split/vague/overlap/safety tests | No systematic sarcasm suite |
| 67 | Duplicate precision/recall on frozen test | `scripts/evaluate_duplicates.py` | WORKS | frozen pair evaluation | Test precision 1/2, recall 1/10; synthetic labels |
| 68 | Safety recall with sample size | safety evaluator and data | WORKS | safety tests/evaluation | Synthetic team-authored cases only |
| 69 | AT-01 to AT-06 pass/fail runner | `scripts/run_acceptance.py` | WORKS | runner output recorded in `docs/TEST_REPORT.md` | Acceptance cases are prototype scenarios |
| 70 | Staff routing pilot | — | ROADMAP | no human pilot run | Needs consenting municipal/staff participants |
| 71 | Failure Gallery | evaluation/relabel pages | MOCK | synthetic examples only | Label as simulated |
| 72 | Label all simulated results visibly | evaluation, analytics, pages | WORKS | UI smoke test and page code | Review any new result surfaces |
| 73 | Highlight input words supporting route | `core/explanation.py` | WORKS | grounded explanation test | None for prototype |
| 74 | Reject ungrounded snippets | `core/explanation.py` | WORKS | grounded rule/text test | Coverage follows assumed responsibility source |
| 75 | Consistent reason-code taxonomy | `core/reviews.py`, `core/duplicates.py` | PARTIAL | structured reason code tests | Taxonomies are not yet unified across all event types |
| 76 | Counterfactual after rerunning model | — | MOCK | no counterfactual experiment | Never imply an unrun comparison |
| 77 | Citizen “why this department” card | portal/explanation UI | PARTIAL | portal page smoke test | Citizen explanation needs end-to-end usability review |
| 78 | Free-text LLM explanations | — | REMOVED | deterministic explanation only | Explicitly excluded to avoid unsupported claims |
| 79 | Per-case reassignment timeline | `core/analytics.py`, replay page | WORKS | timeline tests | Synthetic history only |
| 80 | Reason-code counts and denominators | `core/analytics.py` | WORKS | analytics tests | Counts need real data to interpret |
| 81 | Ownership-gap report by department pair | `core/analytics.py` | PARTIAL | analytics service | No verified ownership sources or representative data |
| 82 | Deadline/delay alerts | — | MOCK | no operational deadline feed | Avoid presenting as real deadlines |
| 83 | Rule-based emerging hotspots with baseline | `core/hotspots.py` | WORKS | equal-window hotspot test | Synthetic counts are not evidence of cause |
| 84 | What-if policy simulator | — | ROADMAP | no implementation | Future research feature |
| 85 | Inbox with channel/language tags | inbox page | WORKS | all-pages AppTest | None for prototype |
| 86 | Review original, fields, suggestion, evidence | review page | WORKS | AppTest and service tests | None for prototype |
| 87 | Compound split view | review/split pages | PARTIAL | split workflow tests | Editing proposed children before save is incomplete |
| 88 | Duplicate candidate panel | `pages/2_Review.py` | WORKS | AppTest and duplicate tests | None for prototype |
| 89 | Ownership banner with rule/source | review pages | PARTIAL | ownership test | Sources remain assumptions |
| 90 | Case replay includes decisions/challenges/duplicate links | `pages/7_Replay.py`, analytics | PARTIAL | replay/ledger tests | Verify complete cross-event chronological view |
| 91 | Evaluation dashboard with labeled metrics | evaluation page | WORKS | all-pages AppTest and evaluation command | Metrics are weak and synthetic |
| 92 | Staff-assisted intake | `pages/1_Intake.py` | WORKS | all-pages AppTest | None for prototype |
| 93 | Operator/reviewer/auditor roles | `core/auth.py` | PARTIAL | RBAC tests | Current registered roles differ; legacy accounts preserved, re-registration required |
| 94 | Timestamped decision history | `core/ledger.py`, replay | WORKS | ledger tests | None for prototype |
| 95 | Exportable audit report | `core/audit.py` | WORKS | masked export access test | Verify export content with deployment operators |
| 96 | Overridden-suggestion report with reasons | analytics pages | PARTIAL | correction/analytics tests | No dedicated aggregate override report |
| 97 | Operator anomaly detection | — | ROADMAP | no anomaly model | Not V1 |
| 98 | Transport and at-rest encryption | attachment/security modules | PARTIAL | attachment encryption tests/status script | SQLite at-rest encryption and deployment TLS are not enabled here |
| 99 | Authentication and service-layer RBAC | `core/auth.py`, services | PARTIAL | role denial tests | Prototype auth, not production identity/security review |
| 100 | Mask names/phone/email in UI | `core/privacy.py` | WORKS | masking and export tests | Validate with real privacy review before real data |
| 101 | Minimize personal data | intake/schema | PARTIAL | privacy-oriented design and tests | No formal retention/data-protection review |
| 102 | Retention and secure deletion | — | ROADMAP | no policy or deletion flow | Define with data controller before deployment |
| 103 | Do not analyze unrelated conversations | intake boundaries | WORKS | workflow has explicit complaint text inputs | Not an independent privacy certification |
| 104 | Live municipal/CPGRAMS integration | — | REMOVED | no live connector | Mock integrations excluded from V1 |
| 105 | Open311-style endpoint | — | MOCK | no live API service | Streamlit app only |
| 106 | Low-bandwidth UI | Streamlit pages | MOCK | not measured | Test on target devices/network |
| 107 | Hindi/English interface toggle | — | MOCK | input language supported; UI translation not built | Separate UI localization task |
| 108 | Save/resume and offline use | — | ROADMAP | no offline-sync workflow | Future deployment feature |
| A | Speech-to-text trial | speech modules/intake page | PARTIAL | UI loads; transcript validation tested | Browser audio transcription and real language quality unverified |
| B | Photo attachments stored privately, authorized only | `core/attachment.py` | WORKS | encryption/access tests | Key lifecycle needs production hardening |
| C | Hotspot signal, baseline and synthetic label | `core/hotspots.py` | WORKS | hotspot regression test | Needs real, sufficient time-series data before use |
| D | Citizen challenge resolved by reviewer, no direct reroute | `core/challenges.py` | WORKS | challenge acceptance and ledger tests | No live citizen pilot |
| E | Role-based access and encryption boundaries | `core/auth.py`, security modules | PARTIAL | RBAC, attachment encryption tests | SQLite encryption and deployment TLS absent; current roles are Public/Government-oriented |
| F | Staged correction capture and offline retraining | correction/retraining modules | PARTIAL | verified-example gate test | No eligible verified labels and no measured before/after model comparison |

## Safety and scope decisions

- No automatic close, reject, deprioritization, finalization, or duplicate merge is permitted. Human decisions are explicitly tested.
- Complaint `original_text` is preserved. Normalization is stored separately.
- Ledger and duplicate decision history have database-level append-only triggers. This is not a cryptographic/hash-chained ledger.
- The responsibility table remains an assumption. All eight existing rows are reported as unverified by the evaluation command.
- The 40 duplicate pairs are synthetic, AI-authored/AI-reviewed labels: 20 dev and 20 frozen test pairs, split by unique group. They are not independent human ground truth.
- TF-IDF threshold selection is validation-only. Since validation misses minimum coverage, the threshold is null and predictions abstain; no fallback threshold is used.
- Existing username accounts are retained as legacy records. They are not silently promoted; accounts must re-register with email for the new auth path.
- The primary SQLite database was backed up before adding the `duplicate_reviews.reason_code` migration. Tests use temporary databases.

## Known repository cleanup

There are stray `_init_.py` files alongside Python's conventional `__init__.py` in `core/`, `data/`, `db/`, `docs/`, and `scripts/`. They are not imported as package initializers and were left in place to avoid deleting user files. `scripts/evaluate.py` and `scripts/evaluate_full.py` serve different reporting paths; neither was removed.

## Geographic access implementation addendum (2026-10-28)

- Staff accounts select state/city/area from `data/geo_coverage.json`; these sample areas are marked simulated. Selection activates immediately in additive `staff_selected_areas`; earlier administrator-approved grants remain in append-only `government_area_events`.
- The login role, active database account, and matching signed-in email are checked before staff scope queries or complaint service operations. Review, challenge, split, duplicate, correction verification, photo reads, analytics, replay, and audit export now receive the staff actor and enforce that scope. Public review requests require complaint ownership.
- Residents can submit fully or partially known structured location; unknown fields remain null. Existing records are not retroactively assigned to an area. Only a configured geography administrator can see the unassigned review queue.
- Test coverage: `tests/test_geo_scope.py`, `tests/test_email_accounts_and_public_portal.py`, and the all-pages AppTest. Final full run: 79 passed.
- Limit: geographic options are illustrative, not authoritative municipal boundaries. Production government identity verification, secure secret deployment, and an authoritative ward dataset are still required.

## Staff self-selection follow-up (2026-10-29)

Ordinary staff can now select one or more areas from Overview without a geography-admin request. `staff_selected_areas` is loaded by the same server-side scope checks for inbox, dashboard, review actions, attachments and related services. Role and operator-department restrictions remain active. Regression tests verify a public Gole Ka Mandir complaint appears immediately after an ordinary staff reviewer/operator selects that area. The previous request/approval flow remains only for old recorded requests; its page is hidden from navigation.
