"""Rate limiting configuration using SlowAPI."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Configure rate limiter keyed by client remote IP address
# Default limit: 60 requests per minute across standard endpoints
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
    headers_enabled=True,
)
