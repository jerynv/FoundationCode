#!/usr/bin/env bash
# free-space.sh — conservative macOS disk-cleanup helper.
#
#   bash free-space.sh           # REPORT only (read-only, deletes nothing)
#   bash free-space.sh --apply   # delete the safe, regenerable junk below
#
# Only ever touches caches, build artifacts, package-manager downloads, Trash
# and Xcode DerivedData. Never your documents or source. Never uses sudo.

set -u
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

size() { du -sh "$1" 2>/dev/null | cut -f1; }

CANDIDATES=(
  "$HOME/Library/Caches"
  "$HOME/Library/Developer/Xcode/DerivedData"
  "$HOME/Library/Developer/CoreSimulator/Caches"
  "$HOME/.Trash"
)

echo "== Disk cleanup =="
df -h / | awk 'NR==1 || /\/$/ {print}'
echo ""
echo "Reclaimable junk (regenerable caches & build artifacts):"
for d in "${CANDIDATES[@]}"; do
  [ -d "$d" ] && printf '  %-52s %s\n' "$d" "$(size "$d")"
done
command -v brew   >/dev/null 2>&1 && echo "  (brew has cached downloads — 'brew cleanup -s' will clear them)"
command -v npm    >/dev/null 2>&1 && printf '  %-52s %s\n' "npm cache" "$(size "$(npm config get cache 2>/dev/null)")"
command -v docker >/dev/null 2>&1 && echo "  (docker: run 'docker system df' to inspect reclaimable space)"
echo ""
echo "10 largest items in your home folder (review manually — NOT auto-deleted):"
du -sh "$HOME"/* "$HOME"/.[!.]* 2>/dev/null | sort -rh | head -10
echo ""

if [ "$APPLY" -eq 0 ]; then
  echo "This was a REPORT — nothing was deleted."
  echo "To delete the safe junk above, run:  bash $0 --apply"
  exit 0
fi

echo "== Applying safe cleanup =="
clear_contents() {            # delete entries INSIDE a dir, keep the dir itself
  [ -d "$1" ] || return 0
  echo "  clearing $1"
  find "$1" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null
}
clear_contents "$HOME/Library/Caches"
clear_contents "$HOME/Library/Developer/Xcode/DerivedData"
clear_contents "$HOME/Library/Developer/CoreSimulator/Caches"
clear_contents "$HOME/.Trash"
command -v brew >/dev/null 2>&1 && { echo "  brew cleanup -s"; brew cleanup -s >/dev/null 2>&1; }
command -v npm  >/dev/null 2>&1 && { echo "  npm cache clean"; npm cache clean --force >/dev/null 2>&1; }
command -v pip3 >/dev/null 2>&1 && { echo "  pip cache purge"; pip3 cache purge >/dev/null 2>&1; }
echo ""
echo "Disk after:"
df -h / | awk 'NR==1 || /\/$/ {print}'
echo "Done. (Caches will rebuild as you use apps.)"
