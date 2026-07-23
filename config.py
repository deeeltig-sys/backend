import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
    DEFAULT_UNIVERSITY_CODE = os.environ.get("DEFAULT_UNIVERSITY_CODE", "USTED")
    PASSWORD_RESET_REDIRECT_URL = os.environ.get(
        "PASSWORD_RESET_REDIRECT_URL", "https://campusmeetx.netlify.app/reset-password.html"
    )
    # flask-cors does NOT split a comma-separated string on its own — passing
    # the raw env var straight through (as this used to) means multiple
    # domains silently never match. Parsed into a real list here instead.
    _cors_raw = os.environ.get("CORS_ORIGINS", "*")
    CORS_ORIGINS = (
        "*" if _cors_raw.strip() == "*"
        else [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]
    )
    FLASK_ENV = os.environ.get("FLASK_ENV", "production")

    @classmethod
    def validate(cls):
        missing = [k for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY") if not getattr(cls, k)]
        if missing:
            raise RuntimeError(
                f"Missing required env vars: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill these in."
            )
