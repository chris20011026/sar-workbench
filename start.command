#!/bin/bash
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  export PYTHONPATH="$PWD/src"
  exec python3 -m alosstack_helper.server
fi
echo 'Please install Python 3.10 or later, then run this launcher again.'
read -r _
