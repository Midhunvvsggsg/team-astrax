import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env if present
load_dotenv(BASE_DIR / '.env')

INSECURE_DEV_SECRET = 'brightloop-hackathon-insecure-key-for-dev'
SECRET_KEY = os.getenv('SECRET_KEY', INSECURE_DEV_SECRET)
DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

# In production (DEBUG=False and not running tests), an explicit secure SECRET_KEY is mandatory
if not DEBUG and 'test' not in sys.argv and (not SECRET_KEY or SECRET_KEY == INSECURE_DEV_SECRET):
    if os.getenv('VERCEL'):
        SECRET_KEY = 'brightloop-production-fallback-secret-key-vercel-2026'
    else:
        raise ImproperlyConfigured("SECRET_KEY must be configured with a secure value when DEBUG=False.")

ALLOWED_HOSTS = [host.strip() for host in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if host.strip()]
for default_host in ('.vercel.app', '.now.sh'):
    if default_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(default_host)

if (DEBUG or 'test' in sys.argv) and 'testserver' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('testserver')

if os.getenv('VERCEL') and '*' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('*')

CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv('CSRF_TRUSTED_ORIGINS', 'https://*.vercel.app').split(',') if origin.strip()
]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Project apps
    'accounts',
    'catalog',
    'subscriptions',
    'invoicing',
    'automation',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'automation.context_processors.demo_clock_context',
            ],
            'builtins': [
                'invoicing.templatetags.finance_tags',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database - SQLite (Local-first with Vercel serverless writable /tmp support)
if os.getenv('VERCEL'):
    import shutil
    tmp_db = Path('/tmp/db.sqlite3')
    bundled_db = BASE_DIR / 'db.sqlite3'
    if not tmp_db.exists() and bundled_db.exists():
        shutil.copy2(bundled_db, tmp_db)
    db_path = tmp_db
else:
    db_path = BASE_DIR / 'db.sqlite3'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': db_path,
    }
}

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation per AstraFlow spec (>8 chars, uppercase, lowercase, special char)
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'accounts.validators.AstraFlowPasswordValidator',
    },
]

# Authentication / Login settings
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles_build' / 'static'

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# Console Email Backend for 100% offline hackathon demo resilience
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'support@brightloop.local'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Security Hardening Settings
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

# Optional SSL Redirect for production deployments
if os.getenv('SECURE_SSL_REDIRECT', 'False').lower() in ('true', '1', 'yes'):
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Demo Clock configuration (admin-only simulated time for presentations & testing)
DEMO_MODE = os.getenv('DEMO_MODE', '0').lower() in ('1', 'true', 'yes')

