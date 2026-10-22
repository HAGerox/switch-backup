#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"

uv venv --python 3.14.9 --clear .build-venv
uv pip install --python .build-venv/bin/python --upgrade \
  "pip==26.2.2" "briefcase==0.4.4" "certifi>=2026.10.19" "gitpython>=3.2.1" "urllib3>=2.8.0" "pytest>=9.0.3,<10"
source .build-venv/bin/activate
python scripts/check-python-security.py
python -m pytest -q
python scripts/prepare-python-support.py

if [[ -d build/switchbackup/macos/app ]]; then
  briefcase update macOS app --update-support --update-requirements --no-input --log
else
  briefcase create macOS app --no-input --log
fi
briefcase build macOS app --no-input --log
app_path=$(find build/switchbackup/macos/app -maxdepth 1 -name '*.app' -print -quit)
python scripts/check-python-security.py "$app_path"
briefcase package macOS app -p dmg --adhoc-sign --no-input --log

app_path=$(find build/switchbackup/macos/app -maxdepth 1 -name '*.app' -print -quit)
project_version=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["tool"]["briefcase"]["version"])')
built_dmg="dist/Switch Backup-${project_version}.dmg"
test -n "$app_path"
test -f "$built_dmg"
unsigned_dmg="${built_dmg%.dmg}-unsigned.dmg"
./scripts/package-macos-dmg.sh "$app_path" "$unsigned_dmg"
mv "$unsigned_dmg" "$built_dmg"
hdiutil verify "$built_dmg"
codesign --verify --deep --strict "$app_path"
if codesign --verify "$built_dmg" >/dev/null 2>&1; then
  echo "The DMG is unexpectedly signed."
  exit 1
fi

echo
echo "Build complete. Look in: ${PWD}/dist"
