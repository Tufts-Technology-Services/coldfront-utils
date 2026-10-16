import functools
import hashlib

from django.core.cache import cache


def ttl_cache(timeout=5 * 60):
    """Cache a function's results in Django's cache framework. Defaults to a 5 minute timeout.

    Results are keyed by the function's module and qualified name and by its arguments, so
    different functions and arguments are cached separately -- including same-named functions in
    different modules. Arguments are identified by their repr(), so they need a stable one; keyword
    arguments are keyed regardless of the order they are passed in. A result of None is not cached.
    """

    def decorator(func):
        key_prefix = f"ttl_cache:{func.__module__}.{func.__qualname__}:"

        @functools.wraps(func)
        def wrapper_cache(*args, **kwargs):
            # Hashed so the key stays short and free of the spaces and control characters that
            # memcached rejects, whatever the arguments contain.
            arguments = repr((args, sorted(kwargs.items()))).encode()
            cache_key = key_prefix + hashlib.sha256(arguments).hexdigest()
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            result = func(*args, **kwargs)
            cache.set(cache_key, result, timeout=timeout)
            return result

        return wrapper_cache

    return decorator
