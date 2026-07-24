#!/usr/bin/env bash
# Build the REAL frozen binary and assert its rendered contract (#38).
# Usage: build_and_probe.sh <workdir> [--break-datas]
#   --break-datas strips the package-data bundling from the spec, recreating
#   the #34 frozen-artifact bug class for the RED proof.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$1"; shift
BREAK="${1:-}"

mkdir -p "$WORK" && cd "$WORK"
cat > run_launcher.py << 'PYEOF'
import sys

from docker_app_launcher.__main__ import main

sys.exit(main())
PYEOF
cp "$REPO/tests/frozen/probe-config.json" launcher.json
python -c "
from docker_app_launcher.pyinstaller import render_spec
spec = render_spec(app_slug='frozen-probe', entry_script='run_launcher.py', icon_path='launcher.json', config_json='launcher.json')
open('launcher.spec', 'w').write(spec)
"
if [ "$BREAK" = "--break-datas" ]; then
    # Recreate the pre-#34 artifact: no package data -> no i18n catalogs.
    python - << 'PYEOF'
spec = open("launcher.spec").read()
broken = spec.replace('+ collect_data_files("docker_app_launcher"),', ",")
assert broken != spec
open("launcher.spec", "w").write(broken)
PYEOF
fi
pyinstaller --noconfirm --log-level ERROR launcher.spec > /dev/null
./dist/frozen-probe --config launcher.json --render-probe
