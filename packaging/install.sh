#!/usr/bin/env bash
set -euo pipefail

applications="$HOME/.local/share/applications"
icons="$HOME/.local/share/icons/hicolor/scalable/apps"
launcher="$HOME/.local/bin/idgent"
case "${1:-}" in
    --uninstall)
        rm -f "$applications/idgent.desktop" "$icons/idgent.svg" "$launcher"
        exit 0
        ;;
    '') ;;
    *) echo "Usage: $0 [--uninstall]" >&2; exit 2 ;;
esac
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$applications" "$icons" "$(dirname -- "$launcher")"
install -m 644 "$repo/packaging/idgent.desktop" "$applications/idgent.desktop"
install -m 644 "$repo/packaging/idgent.svg" "$icons/idgent.svg"
{
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'export PYTHONPATH=%q${PYTHONPATH:+:$PYTHONPATH}\n' "$repo"
    printf 'exec python3 -m idgent "$@"\n'
} > "$launcher"
chmod 755 "$launcher"
