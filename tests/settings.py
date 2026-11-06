"""Django settings for the test suite.

This layers on top of coldfront's own settings rather than hand-rolling a minimal config. These
utilities can't be meaningfully isolated from coldfront: several of them import coldfront's
allocation models, which is also why this module lives outside the package. coldfront_utils
itself is not a Django app (it has no models or templates), so unlike a plugin it is not added to
INSTALLED_APPS; only its task_context app is, below.
"""

from coldfront.config.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# The one part of coldfront_utils that is a Django app: installing it connects its receivers.
INSTALLED_APPS += ["coldfront_utils.task_context"]  # noqa: F405

# ttl_cache stores through django.core.cache; keep that in process and private to each test run.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Keep tests fast and hermetic.
DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
