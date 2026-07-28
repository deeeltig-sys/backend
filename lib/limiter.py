from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Shared limiter instance — imported by app.py (for init_app) and by
# individual route files that need @limiter.limit(...) on specific
# endpoints (auth.py: signup/login/forgot-password).
#
# In-memory storage is the default here, which is fine for a single
# Render instance (WEB_CONCURRENCY=1). If this ever scales to more
# than one worker/dyno, switch storage_uri to a shared backend (e.g.
# Redis) or limits will be tracked per-process instead of globally.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per hour"],
)
