"""Django settings for the test suite.

This layers on top of coldfront's own settings rather than hand-rolling a minimal config. These
utilities can't be meaningfully isolated from coldfront: `coldfront_utils` imports coldfront's
allocation models as soon as it is imported, which is also why this module lives outside the
package. coldfront_utils is not a Django app (it has no models, templates or AppConfig), so unlike
a plugin it is not added to INSTALLED_APPS.
"""

from coldfront.config.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ttl_cache stores through django.core.cache; keep that in process and private to each test run.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Keep tests fast and hermetic.
DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
