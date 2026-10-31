#!/bin/bash
set -e

echo "Installing project dependencies..."
pip install -r requirements.txt

echo "Collecting static assets..."
python3 manage.py collectstatic --noinput --clear

echo "Build complete."
