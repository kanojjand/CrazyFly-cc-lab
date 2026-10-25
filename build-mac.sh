#!/bin/sh
# Собирает dist/CC Lab CrazyFly.app на Mac (Python 3.10+). То же самое делает GitHub Actions.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv-mac
. .venv-mac/bin/activate
python -m pip install -q -r requirements-build.txt pillow
python -m pytest -q
python -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build installer/CrazyflyPilot-mac.spec
ditto -c -k --keepParent "dist/CC Lab CrazyFly.app" "dist/CC-Lab-CrazyFly-mac.zip"
echo "Done: dist/CC Lab CrazyFly.app (zip: dist/CC-Lab-CrazyFly-mac.zip)"
