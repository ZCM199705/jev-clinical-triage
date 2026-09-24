# Clinical triage Brief: final figure release

Version 1.2.0 contains the figure code, numerical source tables and rendered figures used by the final Brief manuscript. The raw response evidence, frozen inputs and original offline analysis environment remain available in the immutable [v1.1.0 release](https://github.com/ZCM199705/jev-clinical-triage/releases/tag/v1.1.0). This version is a manuscript-specific addition to that release, not a standalone copy of its evidence archive.

## Contents

- `figures/manuscript_brief_v4/`: Figure 1, Figure 2, Supplementary Figures S1–S5, editable SVG/PDF, 600-dpi TIFF, PNG previews, legends and numerical source tables.
- `scripts/make_manuscript_figures_brief_v4.py`: rebuilds the final figures and their source tables from the offline workspace created using v1.1.0.
- `scripts/verify_final_release.py`: verifies the evidence archive, repeats the offline analysis, regenerates final figures, compares every published figure CSV byte for byte and checks the principal reported quantities.

## Offline reproduction

Download the v1.1.0 repository source and its `evidence-v1.0.0.tar.gz` attachment. Extract the evidence into a directory containing its `SHA256SUMS.json`, `records.jsonl`, `runs/` and `data/` entries. Install the fixed dependencies in `requirements-lock.txt`. Then run from this version's root:

```sh
python3 scripts/verify_final_release.py \
  --base-release /path/to/v1.1.0/source \
  --data-dir /path/to/evidence \
  --output-dir /path/to/empty/output
```

The output directory must be empty. The verifier runs without API credentials, blocks network access in analysis subprocesses, and writes `FINAL_FIGURE_CHECKS.json` after successful verification. The first-stage v1.1.0 reproduction may take several minutes. Figure image bytes can vary across rendering environments; the numerical CSVs are compared byte for byte.

Use the v1.1.0 release for the request-level data and collection methods, and this version for the final manuscript figure layout and source tables. The original synthetic benchmark is available at [Zenodo](https://doi.org/10.5281/zenodo.18451491).
