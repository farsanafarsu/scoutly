"""Fixed-window rate limits kept in Django's cache."""
import time

from django.conf import settings
from django.core.cache import cache

from .api import ApiError


def client_ip(request):
    if settings.SCOUTLY_TRUST_X_FORWARDED_FOR:
        fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _bucket(key, window):
    return f"rl:{key}:{int(time.time() // window)}"


def count(key, window):
    """How many hits key has in the current window, without adding one."""
    return cache.get(_bucket(key, window)) or 0


def hit(key, limit, window):
    """Count one request against key; True while still within limit for this window."""
    bucket = _bucket(key, window)
    if cache.add(bucket, 1, window + 5):
        return True
    try:
        return cache.incr(bucket) <= limit
    except ValueError:  # expired between add and incr
        cache.add(bucket, 1, window + 5)
        return True


def limit(request, scope):
    """Apply the hourly limit for scope (see SCOUTLY_RATE_LIMITS), per account or per IP."""
    anon, user = settings.SCOUTLY_RATE_LIMITS[scope]
    if request.user.is_authenticated:
        who, n = f"u{request.user.pk}", user
    else:
        who, n = f"ip{client_ip(request)}", anon
    if not hit(f"{scope}:{who}", n, 3600):
        raise ApiError("Too many requests in the last hour. Please wait a little and try again.", status=429)
