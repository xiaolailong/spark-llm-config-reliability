#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/4] Audit authoritative 540-row master"
python3 "${SCRIPT_DIR}/78_reaudit_formal_master_v011.py"

echo "[2/4] Reproduce Primary statistics"
Rscript "${SCRIPT_DIR}/80_formal_statistics_v012.R"

echo "[3/4] Rebuild and verify sensitivity master from public adjudication ledger"
python3 "${SCRIPT_DIR}/89_rebuild_sensitivity_master_public_v01.py"

echo "[4/4] Reproduce sensitivity statistics"
Rscript "${SCRIPT_DIR}/90_sensitivity_statistics_v01.R"

echo "PASS: final Primary + Sensitivity statistical analyses reproduced."
