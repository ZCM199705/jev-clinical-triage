# Figure and table legends | Brief selection

## Shared definitions

The frozen benchmark contains 30 main base scenarios (960 input variants per model) and four emergency-supplement scenarios (128 variants). Agreement means that a valid grade belongs to its input's source-derived acceptable-label set. Technical failures remain in planned denominators. Repeated calls and variants are not independent cases. The first-round single-grade reference Jev–Luna comparison is the prespecified primary analysis; other strata and format comparisons are descriptive or exploratory.

## Figure 1 | Clinical triage performance and reproducibility

**a,** First-round reference agreement in single-grade reference (15 scenarios; 480 inputs), all main (30; 960), and two-grade reference (15; 480) strata for the four current API models. Points are scenario-equal estimates with failures retained. **b,** Jev minus Luna agreement in single-grade reference inputs across three rounds. Bars are 95% scenario-cluster bootstrap intervals (5,000 resamples; seed 20260922); round 1 (asterisk) is the prespecified primary analysis. The round-specific differences are 10.83 [2.92, 19.79], 10.42 [2.29, 19.58] and 9.17 [1.04, 17.92] percentage points. The first-round Jev–Gemini and Jev–DeepSeek secondary comparisons each have Holm-adjusted P=0.085938. **c,** First-round error directions and technical failures among 960 planned main requests per model. **d,** Three-round decision categories among the same 960 planned inputs: identical reference-agreeing, identical reference-disagreeing, changed but always reference-agreeing, changed with at least one reference-disagreeing round, and unassessable after a technical failure. The right-hand rate is identical decisions divided by inputs valid in all three rounds; Jev had 741, 145, 33, 27 and 14 inputs in the five categories, respectively.

## Figure 2 | Selective prediction and operational profile

**a,** Jev first-round disagreement risk versus coverage, conditional on 476 valid probability-bearing single-grade reference inputs (480 planned). Equal-probability inputs enter together. Thresholds are descriptive; at probability ≥0.90, 152 inputs from 11 scenarios were retained, with eight disagreements. Empty selection has undefined risk. **b,** First-round single-grade reference agreement versus measured HTTP p50 latency at concurrency ceiling 1. The three lighter symbols per model represent three complete time blocks of 200 formal requests; the larger symbol is the median HTTP p50 at that model's first-round agreement. Block symbols have a fixed area and the larger, darker median symbols have a fixed area. Cost figures, including the DeepSeek upper estimate and one unknown Jev charge, are reported in Supplementary Table S3. Warmups and admission waiting are excluded from HTTP latency. Probability reliability bins, Brier and NLL are retained in Supplementary Calibration Data.

## Table 1 | Four-model summary

Single-grade reference agreement is correct / 480 first-round planned inputs. Three-round consistency is identical decisions / main inputs with valid answers in every round. Undertriage uses valid first-round main outputs. Service columns are medians of three concurrency-1 cells (200 measured requests per cell), with warmups excluded. Costs include known failed-request charges; DeepSeek is an upper estimate and one Jev charge is unknown. These denominators differ by column.

## Supplementary Figure S1 | Repeated-run agreement

**a,** Agreement across all main scenarios over three rounds (30 base scenarios). **b,** Three-round descriptive means for single-grade versus two-grade reference scenarios; whiskers show the observed minimum–maximum across rounds, not confidence intervals. The full per-round values and denominators are in the source data. Repeated rounds use the same cases.

## Supplementary Figure S2 | Explicit-D emergency error patterns

D→non-D predictions / valid D-labelled inputs in two main and four emergency-supplement base scenarios, for all models and rounds. Each cell has 32 input variants, and D→C is undertriage. The four supplement scenarios had no such errors. Rows are base scenarios, not independent sets of 32 patients.

## Supplementary Figure S3 | Sensitivity to information and wording

**a,** Objective minus subjective agreement, averaging the three round-specific paired differences by model; symbols use complete valid pairs. Whiskers show round ranges. Each information version is scored against its own reference set; the selection starts with 480 pairs per model and round, before technical losses. **b,c,** Three-round descriptive means of decisions changed by anchoring, access barrier, gender wording and explicit Black wording. Colour uses one shared 0–35% scale and denominators are valid single-factor matched pairs (480 planned per factor, model and round). Other error-direction measures and each round's counts remain in source data. Explicit Black is compared with race unspecified; this does not estimate real-population differences.

## Supplementary Figure S4 | Service performance across concurrency

HTTP p50, p95, valid-output throughput, peak in-flight HTTP requests, admission-wait p50 and technical failure percentage across concurrency ceilings 1, 4, 8, 16 and 32. There are three complete time-block cells per model and ceiling, for 60 cells and 12,000 formal requests; 300 warmups are excluded. Lines are medians and shading is the observed block range, not a confidence interval. Jev's frozen 0.06-s admission interval caps offered throughput at approximately 16.67/s under this protocol.

## Supplementary Figure S5 | Noncontemporaneous ChatGPT Health reference

**a,** Historical ChatGPT Health web-product agreement (9–11 January 2026). **b,** First-round agreement of the four API models (September 2026). **c,** Error-direction composition on the 960 main inputs, displayed with the historical group separated. Historical grades are the original authors' `llm_triage` codes rescored against the current acceptable-label sets; normalized clinical content and references matched for all 960 main and 128 emergency-supplement inputs. Full prompts, product packaging and response coding differ. This post hoc historical reference is excluded from prespecified testing and is not independent clinical validation. The original-paper two-grade-scenario percentage discrepancy is recorded once in Supplementary Table S1.

## Supplementary Table S1 | Historical product and current API methods

Collection period, interface, input matching, output coding, reference scoring, repeated measures and unavailable metrics are tabulated. The published two-grade-scenario text states 96.0%; the released CSV gives 462/480 (96.25%) by acceptable-set scoring or 477/480 (99.375%) by minimum-grade-only scoring. This unresolved difference is limited to the historical audit and does not affect the current-model primary comparison.

## Supplementary Table S2 | Output-format sensitivity

Values are acceptable / valid or codable / planned outputs, with the comparison population shown separately for each format. Structured and explanation JSON each use 60 main inputs per model; one explanation sample is compared with the first structured round. Natural-language consensus includes 60 main and eight emergency inputs per model and is an exploratory two-model coding convention. Percentages across formats are not presented as a randomized causal comparison; the direction varied by model.

## Sources

Original Methods: https://www.nature.com/articles/s41591-026-04297-7 . Public historical data: https://github.com/ashwinra-code/gpt-health-eval . Frozen manifests, response evidence, analysis code and numerical source tables are retained in the project. The figure-selection script was written after collection; no new model API calls were made.
