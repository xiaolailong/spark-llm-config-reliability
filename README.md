# Spark Configuration Reliability — Reproducibility Code and Analysis

This repository contains the public code, frozen Formal90 benchmark,
authoritative compact analysis datasets, and final statistical analyses for:

**Do LLMs Configure Apache Spark Correctly? An Execution-Grounded Empirical
Evaluation of Spark Configuration Generation and Repair**

> Paper status: manuscript in preparation.  
> Full archival evidence: https://doi.org/10.5281/zenodo.23121320

## What is included

- `01_benchmark/`: frozen 90-task benchmark and real-case provenance.
- `02_method/`: frozen context metadata, parser, validator, safe command builder,
  runtime probe, repair protocol, and parser-fix audit.
- `04_analysis/formal_master_v01/`: authoritative 540-row Initial analysis dataset.
- `04_analysis/formal_statistics_v012/`: final Primary statistical outputs.
- `04_analysis/sensitivity_v01/`: frozen sensitivity policy/adjudication,
  rescored dataset, and final sensitivity statistics.
- `04_analysis/scripts/`: final build/audit/statistical reproduction scripts.

The large sealed raw execution/model-evidence ZIP files are intentionally not
stored in GitHub. They are part of the full Zenodo archival package.

## Analysis design

Formal90 contains 90 unique tasks. Each task is evaluated with three models
under two conditions, giving 540 Initial model-condition-task observations.
These 540 rows are **not** treated as 540 independent tasks.

Primary scoring uses the frozen strict task-conforming criterion. A separate
pre-specified benign-extra sensitivity analysis does not overwrite Primary
outcomes.

## Reproduce the published statistical analyses

Required R packages:

```text
lme4
emmeans
geepack
```

Run from the repository root:

```bash
bash 04_analysis/scripts/run_reproduce_statistics.sh
```

The wrapper:

1. audits the frozen 540-row Primary master;
2. reproduces Primary statistics;
3. reconstructs the sensitivity master from the public adjudication ledger and
   checks it against the frozen sensitivity master;
4. reproduces the Sensitivity statistics.

No model API calls are required.

## Environment

See `ENVIRONMENT.txt`.

## Integrity

`SHA256SUMS.txt` contains SHA-256 hashes for all files in this GitHub release.
`RELEASE_MANIFEST.json` records the exact release contents.

## Citation

See `CITATION.cff`. After the Zenodo record is published, replace the DOI
placeholder in this README and, if desired, add the DOI to `CITATION.cff`.

## Licensing

See `LICENSES.md` and `THIRD_PARTY_NOTICE.md`.

## Archival data

Full sealed execution evidence and the complete reproducibility bundle are
archived at:

**https://doi.org/10.5281/zenodo.23121320**
