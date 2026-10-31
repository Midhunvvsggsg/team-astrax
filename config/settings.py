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
if not DEBUG and 'test' not in sys.argv:
    if not SECRET_KEY or SECRET_KEY == INSECURE_DEV_SECRET:
        raise ImproperlyConfigured("SECRET_KEY must be configured with a secure value when DEBUG=False.")

ALLOWED_HOSTS = [host.strip() for host in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if host.strip()]
if DEBUG or 'test' in sys.argv:
    if 'testserver' not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append('testserver')

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

# Database - SQLite (Local-first)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation per Brightloop spec (>8 chars, uppercase, lowercase, special char)
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'accounts.validators.BrightloopPasswordValidator',
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

