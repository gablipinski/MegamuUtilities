#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
REQUIREMENTS_FILE="${ROOT_DIR}/requirements.txt"

find_python() {
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        echo "${PYTHON_BIN}"
        return 0
    fi

    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

if ! PYTHON_BIN="$(find_python)"; then
    echo "[ERROR] No compatible Python interpreter was found. Install Python 3.11+ and try again." >&2
    exit 1
fi

echo "[INFO] Using Python interpreter: ${PYTHON_BIN}"
echo "[INFO] Creating virtual environment in ${VENV_DIR}"

if [[ ! -d "${VENV_DIR}" ]]; then
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip

if [[ -f "${REQUIREMENTS_FILE}" ]]; then
    TMP_REQUIREMENTS="$(mktemp)"
    trap 'rm -f "${TMP_REQUIREMENTS}"' EXIT
    grep -vE '^(#|$|winotify|winsdk)' "${REQUIREMENTS_FILE}" > "${TMP_REQUIREMENTS}"
    python -m pip install -r "${TMP_REQUIREMENTS}"
fi

echo "[OK] Virtual environment is ready."
echo "[INFO] Activate it with: source ${VENV_DIR}/bin/activate"
echo "[INFO] Start the app with: ./scripts/shell/run_app.sh"
