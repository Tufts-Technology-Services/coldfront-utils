"""Django settings for the test suite.

This layers on top of coldfront's own settings rather than hand-rolling a minimal config. These
utilities can't be meaningfully isolated from coldfront: several of them import coldfront's
allocation models, which is also why this module lives outside the package. coldfront_utils
itself is not a Django app, so unlike a plugin it is not added to INSTALLED_APPS; only its two
apps are, task_context and allocation_request, below.
"""

from pathlib import Path

from coldfront.config.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# The parts of coldfront_utils that are Django apps: task_context connects its receivers, and
# allocation_request makes ColdFront's request page use its form.
INSTALLED_APPS += ["coldfront_utils.task_context", "coldfront_utils.allocation_request"]  # noqa: F405

# Card templates for the sample sections in tests/test_allocation_request.py, as a plugin would ship.
TEMPLATES[0]["DIRS"] = [*TEMPLATES[0]["DIRS"], str(Path(__file__).resolve().parent / "templates")]  # noqa: F405

# ttl_cache stores through django.core.cache; keep that in process and private to each test run.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Keep tests fast and hermetic.
DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
