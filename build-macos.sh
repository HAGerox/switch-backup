#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "briefcase==0.4.4"

briefcase create macOS app
briefcase build macOS app
briefcase package macOS app -p dmg --adhoc-sign

app_path=$(find build/switchbackup/macos/app -maxdepth 1 -name '*.app' -print -quit)
built_dmg=$(find dist -maxdepth 1 -name '*.dmg' -print -quit)
test -n "$app_path"
test -n "$built_dmg"
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
