"""테이블 생성. 사용법: python scripts/init_db.py"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import Base, engine
from app.rag.search import ensure_fts_table
from app.database import SessionLocal

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    ensure_fts_table(db)
    db.close()
    print("DB 테이블 생성 완료 (rag_chatbot.db)")
