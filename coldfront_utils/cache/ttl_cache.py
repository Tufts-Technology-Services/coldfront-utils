import functools
from django.core.cache import cache


def ttl_cache(timeout=5*60):
    """cache of previous function calls using django cache framework. defaults to 5 minute timeout. 
    Cache key is generated from function name and arguments, 
    so different functions and arguments will be cached separately."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper_cache(*args, **kwargs):
            cache_key = func.__name__ + str(args) + str(tuple(kwargs.items()))
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            result = func(*args, **kwargs)
            cache.set(cache_key, result, timeout=timeout)
            return result
        return wrapper_cache
    return decorator
