# Dataset Card - NagrikSetu synthetic complaints (Gwalior setting)
- **What it is:** 96 fictional civic complaints written for a Gwalior, Madhya Pradesh setting. Tag: SYNTHETIC. Not real municipal data, not representative of real complaint volume or mix.
- **Locality names:** real Gwalior area names used only as scene-setting (Thatipur, Morar, Hazira, DD Nagar, Kampoo, Phool Bagh, Maharaj Bada, Jayendraganj, Gole Ka Mandir, Jhansi Road, Tansen Road, Sirol Road, City Centre, Station Road). No real people, addresses or complaint records.
- **Structure:** 32 complaint families x 3 language versions (English, Hinglish, Hindi in Devanagari) = 96 rows. 24 single-issue families, 4 compound (two issues), 4 vague/missing-information. Two families are overlap cases (waterlogging on a road; burst pipe flooding a road); manhole is also an overlap.
- **Labels:** `owner_label` (department, or MULTI / UNCLEAR), `sub_labels` for compound rows, `has_overlap`. Labels were written by an AI assistant (Claude), then re-checked by the same assistant in a second pass (structure checks: consistent labels across the 3 language versions, valid department names, compound rows have 2+ sub-labels, Devanagari rows are Devanagari, no duplicate texts, split by family). This is NOT independent human labelling: the same source wrote the text and the labels, so there is circularity risk. Column `labeller` = AI_LABELLED_AI_REVIEWED. Do not describe this as human-verified or double-annotated.
- **Responsibility table:** department grouping is an ASSUMPTION. Only the existence of a GMC grievance service and a water/sewerage service was confirmed via services.india.gov.in. TODO: verify with GMC.
- **Split:** by family (so the three versions of one complaint never straddle train/val/test). train 45 / val 24 / test 27 (test: 6 Roads, 6 Drainage, 3 each Sanitation/Electricity/Water, 3 compound, 3 vague). Test set frozen on 2026-10-28; `test_split_manifest.json` checks its family list, row count, and SHA-256 before Engine 8 reports results.
- **Keywords:** the baseline keyword list was written alongside the data, so baseline scores are likely optimistic. TODO: rebuild from charter terms.
- **Known limits:** small n (9 test rows per language for single-owner is even smaller); Hinglish spelling variation is narrow; no sarcasm, no long narratives, no photos or voice.

## Safety rubric sanity set
- `safety_test_cases.csv` contains 12 team-authored synthetic examples (10 rubric-positive and 2 negative) across English, Hindi and Hinglish. The wording was selected to exercise the configured team-proposed terms, including calm reports.
- These rows test rule behavior only. They are not independently labelled, representative safety reports, or evidence of real-world safety recall. The evaluation page displays their actual numerator and denominator under a synthetic sanity-check label.

## Duplicate benchmark
- `duplicate_pairs.csv` contains 40 synthetic labelled pairs: 20 dev and 20 frozen test, with 40 unique incident groups and no group crossing. Labels are AI-authored/AI-reviewed, not independently human-labelled; all locations and examples are synthetic. Treat measured metrics as a software sanity check only.
- `duplicate_test_manifest.json` freezes the test rows by hash. `duplicate_threshold.json` records the threshold chosen from dev only, the dev hash, and whether the preferred precision floor was met. Re-tune only on dev. A change to the test manifest requires an explicit data-change note and a newly governed evaluation, not silent threshold adjustment.
- Candidate features use Devanagari-safe TF-IDF word 1–2 grams on normalized text, shared extracted issue labels, normalized location relation, a configurable time window, and explicit guard flags. Location proximity is string-based and is not geospatial. A candidate score only ranks cases for human review; it is not a probability or duplicate decision. Cross-language matching (especially Hindi-English) may miss a related pair.
- Ranking weights and the minimum candidate signal are clearly labelled placeholders in `core/config.py`; the evaluation threshold artifact is tuned only on development pairs, and the frozen test manifest is verified before scoring. Never tune against the frozen test rows.
- The benchmark includes the Ward 12 park streetlight example and multiple look-alike but distinct pairs (same wording on different days, same place with different issue, or different nearby landmarks). Current test metrics are in `docs/TEST_REPORT.md`; treat them as weak software checks, not validated duplicate detection.

## Geographic scope options
- `geo_coverage.json` contains selectable sample geographies for four states and selected cities/localities, including Gwalior's Morar and Gole Ka Mandir. These options are SIMULATED prototype coverage, not an authoritative ward register. Complaint area access depends on structured values and a persisted verified staff grant; a staff member's selection alone never grants access.

## Pseudo staff directory
- `staff_directory.csv` is a pseudo staff directory, simulated, not a real government directory. Its 24 fictional records use only `@staff.nagriksetu-demo.example` addresses and are tagged `SYNTHETIC`. The list supplies test roles/departments only; it proves no real person's identity, employment, or authority.

## Labelling judgement calls (assumptions, not facts)
- Dead animal on a road -> Sanitation (road keyword would send it to Roads).
- Footpath / speed breaker -> Roads. Community toilet cleaning -> Sanitation.
- Hanging electric wire and streetlights -> Electricity (real ownership may split between the municipal corporation and the power company).
- Waterlogging on a road -> Drainage, with Roads as overlap. Burst water pipe flooding a road -> Water Supply, with Roads as overlap. Missing manhole cover -> Drainage, with Roads as overlap.
- Streetlights left on in daytime -> Electricity.
