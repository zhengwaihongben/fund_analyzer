#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 not found."
    exit 1
fi

# 建立專案內虛擬環境，與 install.bat / launch.vbs 保持一致
if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtual environment .venv ..."
    python3 -m venv .venv
fi

./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

echo "Done. Run: ./.venv/bin/python fund_analyzer_gui.py"
