#!/bin/bash
set -e

echo "=== Installing Python dependencies ==="
python3 -m pip install -r requirements.txt --break-system-packages 2>/dev/null || python3 -m pip install -r requirements.txt

echo "=== Collecting Static Assets ==="
python3 manage.py collectstatic --noinput --clear

echo "=== Build Complete ==="
