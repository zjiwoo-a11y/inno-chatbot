from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_light_migrations():
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(document_chunks)")).fetchall()]
        if cols and "category" not in cols:
            conn.execute(text("ALTER TABLE document_chunks ADD COLUMN category VARCHAR(100) DEFAULT ''"))
            conn.commit()
        user_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()]
        if "email" in user_cols and "username" not in user_cols:
            conn.execute(text("ALTER TABLE users RENAME COLUMN email TO username"))
            conn.commit()
