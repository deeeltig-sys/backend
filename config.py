import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
    DEFAULT_UNIVERSITY_CODE = os.environ.get("DEFAULT_UNIVERSITY_CODE", "USTED")
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
    FLASK_ENV = os.environ.get("FLASK_ENV", "production")

    @classmethod
    def validate(cls):
        missing = [k for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY") if not getattr(cls, k)]
        if missing:
            raise RuntimeError(
                f"Missing required env vars: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill these in."
            )
