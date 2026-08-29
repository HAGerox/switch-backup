#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "briefcase==0.4.4"
briefcase dev
