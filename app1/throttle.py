"""Login throttling backed by the Django cache.

Failures are counted per username and per client address within a sliding window.
Behind a reverse proxy REMOTE_ADDR is the proxy's address, so the per-address limit
is deliberately higher than the per-username limit.
"""
import hashlib

from django.conf import settings
from django.core.cache import cache


def _keys(request, username):
    user_hash = hashlib.sha256((username or "").strip().lower().encode()).hexdigest()
    address = request.META.get("REMOTE_ADDR", "unknown")
    return f"login-fail:user:{user_hash}", f"login-fail:addr:{address}"


def is_locked_out(request, username):
    user_key, addr_key = _keys(request, username)
    return (
        cache.get(user_key, 0) >= settings.LOGIN_THROTTLE_MAX_FAILURES
        or cache.get(addr_key, 0) >= settings.LOGIN_THROTTLE_MAX_FAILURES_PER_IP
    )


def register_failure(request, username):
    for key in _keys(request, username):
        cache.add(key, 0, settings.LOGIN_THROTTLE_WINDOW_SECONDS)
        try:
            cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.set(key, 1, settings.LOGIN_THROTTLE_WINDOW_SECONDS)


def reset(request, username):
    user_key, _ = _keys(request, username)
    cache.delete(user_key)


def allow_rate(key, interval_seconds):
    """Return True if an action under ``key`` may run now (at most once per interval)."""
    if interval_seconds <= 0:
        return True
    return cache.add(f"rate:{key}", 1, interval_seconds)
