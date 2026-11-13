import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_CHAT_MODEL: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    OPENAI_FALLBACK_MODEL: str = os.getenv("OPENAI_FALLBACK_MODEL", "gpt-4o-mini")
    OPENAI_EMBED_MODEL: str = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "dev-secret-change-me")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./rag_chatbot.db")

    TOP_K: int = int(os.getenv("TOP_K", "5"))
    MIN_SCORE_THRESHOLD: float = float(os.getenv("MIN_SCORE_THRESHOLD", "0.15"))

    SQL_METADATA_SOURCE: str = os.getenv("SQL_METADATA_SOURCE", "")
    SQL_METADATA_SOURCES: str = os.getenv("SQL_METADATA_SOURCES", "")
    SQL_DEFAULT_SYSTEM: str = os.getenv("SQL_DEFAULT_SYSTEM", "pf")
    SQL_METADATA_TTL: int = int(os.getenv("SQL_METADATA_TTL", "60"))

    FIRST_ADMIN_ID: str = os.getenv("FIRST_ADMIN_ID") or os.getenv("FIRST_ADMIN_EMAIL", "admin")


settings = Settings()
