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

built_dmg=$(find dist -maxdepth 1 -name '*.dmg' -print -quit)
test -n "$built_dmg"
unsigned_dmg="${built_dmg%.dmg}-unsigned.dmg"
hdiutil convert "$built_dmg" -format UDZO -o "$unsigned_dmg"
mv "$unsigned_dmg" "$built_dmg"
if codesign --verify "$built_dmg" >/dev/null 2>&1; then
  echo "The DMG is unexpectedly signed."
  exit 1
fi

echo
echo "Build complete. Look in: ${PWD}/dist"
