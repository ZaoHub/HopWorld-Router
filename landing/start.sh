#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -c 'import sys; sys.exit("需要 Python 3.10 或更新版本。") if sys.version_info < (3, 10) else None'
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import paramiko; assert paramiko.__version__ == "5.1.0"' 2>/dev/null; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi
exec .venv/bin/python installer.py "$@"
