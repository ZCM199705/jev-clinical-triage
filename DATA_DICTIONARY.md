# Public evidence dictionary

`records.jsonl` has one row per planned formal-stage request. `key` is the frozen unique job digest. `metadata` links the model role, input version, request digest and stage-specific fields to a freeze. `status=terminal` describes job completion, whereas `result.status` distinguishes success, parsing failure and request failure. `amount_usd` is a permanent budget reservation, not expenditure.

`result.parsed` stores the category and, for JEV, returned probabilities. Some format outputs instead retain `assistant_text`. `cost_usd=null` is an unknown charge; `cost_status` distinguishes provider reports and estimates. Response paths are relative to the reproduction workspace. `response_file_sha256` refers to the released evidence wrapper.

Evidence files contain `job`, `request_payload`, `raw_response` and `result`. `raw_response` is the original HTTP response-body string. Provider identifiers are service/model metadata, not clinical labels. `response_provenance.json` links the original and published wrapper hashes, plus a hash of the exact raw-response string. Rebasing local paths does not imply the original acquisition wrappers were byte-identical to all published wrappers.

`freezes/*/cases.jsonl` defines case identity, base scenario, information version, factor combination, clinical text, acceptable labels and precollection applicability flags. A–D run from least to most urgent. Acceptable adjacent-label sets use membership scoring, not merely reaching a minimum grade. Race perturbations compare explicit Black wording with unspecified race.

`freezes/*/jobs.jsonl` preserves every planned job. First/second/third rounds each have 4,352. Format control has 408; natural coding has 408 coder jobs for 204 source answers. Efficiency has 12,300 jobs across 60 cells: 200 measured and five warmup requests per cell. It excludes warmups from service endpoints and uses complete valid timing cells only.

`runs/efficiency_v1/cells/*.json` contains phase boundaries, completion flags, wall-clock measurements, in-flight peaks and hashes. Valid-response HTTP latency excludes admission waiting; throughput includes unit overhead. Three block summaries use medians and observed ranges, not independent-request inference.

`index_metadata.json` retains the numerical budget baseline needed to reproduce cost summaries, without account credentials. Pilot response data are excluded; their previously reported aggregate cost and permanent reserve are labelled as prior quantities.

The historical CSVs retain source-author output coding and synthetic case text. They are matched by composite input keys and checked against clinical text and reference sets; they are not newly clinically adjudicated.
