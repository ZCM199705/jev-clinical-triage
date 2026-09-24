# Table 1 | Benchmark and service characteristics

| Model | Single-grade reference agreement, n/N (%) | Three-round consistency, n/N (%) | Undertriage / valid main outputs | HTTP p50, s | Known US$ / 1,000 requests |
|---|---|---|---|---:|---:|
| Jev | 353/480 (73.54) | 886/946 (93.66) | 17/953 | 0.404 | 0.028713 |
| Luna | 301/480 (62.71) | 817/960 (85.10) | 36/960 | 1.244 | 0.085731 |
| Gemini | 285/480 (59.38) | 865/960 (90.10) | 46/960 | 0.732 | 0.106182 |
| DeepSeek | 292/480 (60.83) | 651/959 (67.88) | 21/959 | 1.024 | 0.119166† |

Single-grade reference agreement: first round, 15 base scenarios and 480 planned inputs per model; failures remain in the denominator. Consistency: identical decisions across three valid rounds, among 960 planned main inputs. Undertriage: first round, 30 base scenarios, valid outputs only; failure counts are separately retained in Figure 1c. Service metrics: concurrency 1, medians of three complete time-block cells, 200 measured requests per cell; warmups excluded. HTTP latency excludes admission waiting. Cost includes known charges for failed requests; unknown charges are not zero. † DeepSeek uses a peak uncached upper estimate; other models use provider-reported charges. These are neither reconciled invoices nor clinical cost-effectiveness estimates.

Unknown charges in the concurrency-1 subset: Jev 1 request; other models 0. Jev cost values summarize known charges only, including in the affected block; the unknown amount is not imputed as zero.
