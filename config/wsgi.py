"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os
import shutil
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
app = application

# Automated Vercel Serverless ephemeral database bootstrap
if os.getenv('VERCEL'):
    try:
        from django.conf import settings
        from django.core.management import call_command

        tmp_db = Path('/tmp/db.sqlite3')
        bundled_db = settings.BASE_DIR / 'db.sqlite3'

        if bundled_db.exists() and not tmp_db.exists():
            shutil.copy2(bundled_db, tmp_db)
        elif not tmp_db.exists():
            call_command('migrate', verbosity=0)
            call_command('seed_demo', subscriptions=50, verbosity=0)
    except (OSError, RuntimeError) as e:
        print(f"Vercel DB initialization warning: {e}")
