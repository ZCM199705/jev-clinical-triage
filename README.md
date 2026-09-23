# Clinical triage API evaluation

Offline reproduction of a four-model evaluation using the published synthetic ChatGPT Health triage benchmark. This repository contains research code and data, not a clinical decision service. Model responses are machine-generated research observations and may disagree with the source reference labels.

The release covers three structured-output rounds (13,056 requests), output-format collection (408), automatic coding (408), and service measurements (12,300, including 300 warmups). The separate engineering pilot is excluded. Reference labels were inherited; no new physician adjudication was conducted. The historical web-product records are not a contemporaneous fifth API group.

## Reproduce without API keys

Use Python 3.12 on macOS or Linux. Install dependencies once; the analysis itself denies network connections.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
```

Download `evidence-v1.0.0.tar.gz` and `SHA256SUMS` from the [v1.0.0 release](https://github.com/ZCM199705/jev-clinical-triage/releases/tag/v1.0.0). Verify the archive before extracting it:

```sh
shasum -a 256 -c SHA256SUMS
tar -xzf evidence-v1.0.0.tar.gz
python reproduce.py --data-dir evidence-v1.0.0 --output-dir reproduction
```

The output directory must be empty. Expect several minutes, approximately 1 GB of disk space and font-dependent plot rendering. Arial is used if available; Matplotlib may substitute a local sans-serif font. Numeric comparisons do not depend on fonts. `--skip-figures` still recomputes all numeric analyses. No API key, original SQLite file or original machine path is required.

`REPRODUCTION_CHECKS.json` records comparisons with the archived numeric references. `logs/` contains per-stage logs; `reports/` contains regenerated JSON, CSV and figures. The launcher builds a disposable SQLite compatibility index **from public JSONL** for the original audited readers. It does not copy or require the private acquisition ledger. It also blocks socket connections and removes credential-like environment variables from analysis subprocesses.

To run the included unit tests after materialization:

```sh
cd reproduction
python -m pytest -q tests
```

## Layout

- `runtime/`, `scripts/run_*.py`: acquisition and parsing implementation; live execution is disabled by configuration and requires separately supplied credentials.
- `freezes/`: model IDs, options, complete input grids, request digests and frozen rules. One path-bearing coding manifest is rebased for publication; see `EXPORT_CHANGES.json`.
- `analysis/`, `postanalysis/`: reference-set scoring, scenario-level bootstrap, paired tests, complete-pair analyses, probability scores and consensus coding.
- `expected/`: original numeric reference outputs for verification; these are checked against newly computed results, not used as a substitute for recomputation.
- Evidence attachment: per-request JSONL records, request/response evidence, complete unit timing, public historical CSVs, provenance and checksums.
- `DATA_DICTIONARY.md`, `THIRD_PARTY_NOTICES.md`: definitions, source attribution and licensing boundaries.

The primary comparison was the first-round JEV–Luna clear-label contrast. The internal freeze was not a public preregistration. Exact secondary testing and combined-round analysis were implemented after collection. Repeated inputs remain clustered by base scenario. Failed requests remain in planned denominators; unknown charges are not zero.

## Version and citation

Version 1.0.0 is the initial reproducibility release. Cite the versioned URL and the original benchmark (DOI: 10.5281/zenodo.18451491). Formal authorship metadata and a Zenodo DOI are pending; none is implied by the hosting account. The manuscript and author declarations are not included in this repository.

Own code: MIT. Own derived research data: CC BY 4.0 to the extent rights exist. Original benchmark: CC0. Third-party material and model responses are subject to the boundaries in `THIRD_PARTY_NOTICES.md`; no model weights or provider software are distributed.
