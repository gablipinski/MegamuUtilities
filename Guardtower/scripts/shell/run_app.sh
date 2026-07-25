#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "[ERROR] Virtual environment not found. Run ./scripts/shell/initvenv.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

cd "${ROOT_DIR}/src"
if [[ "$*" != *"--gui"* ]] && [[ "$*" != *"--no-gui"* ]]; then
    exec python main.py --gui "$@"
else
    exec python main.py "$@"
fi
