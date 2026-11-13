import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey, ForeignKeyConstraint
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    messages = relationship("ChatMessage", back_populates="user")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    cited_chunk_ids = Column(Text, default="")
    was_answerable = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    user = relationship("User", back_populates="messages")


class UnansweredQuestion(Base):
    __tablename__ = "unanswered_questions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question = Column(Text, nullable=False)
    best_score = Column(String(20), default="0.0")
    status = Column(String(20), default="open")
    resolution_note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    user = relationship("User")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    source_path = Column(String(500), nullable=False)
    heading_path = Column(String(500), default="")
    content = Column(Text, nullable=False)
    embedding_json = Column(Text, nullable=False)
    category = Column(String(100), default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class SqlSource(Base):
    __tablename__ = "sql_sources"

    system_id = Column(String(50), primary_key=True)
    kind = Column(String(10), nullable=False, default="google")
    sheet_url = Column(String(500), default="")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
