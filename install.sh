#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 not found."
    exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "Done. Run: python3 fund_analyzer_gui.py"