import json
import math
import re
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import DocumentChunk


def ensure_fts_table(db: Session):
    db.execute(text("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content, heading_path, content='document_chunks', content_rowid='id'
        )
    """))
    db.commit()


def index_chunk_for_fts(db: Session, chunk: DocumentChunk):
    db.execute(
        text("INSERT INTO chunks_fts(rowid, content, heading_path) VALUES (:id, :content, :heading)"),
        {"id": chunk.id, "content": chunk.content, "heading": chunk.heading_path},
    )


def remove_chunk_from_fts(db: Session, chunk: DocumentChunk):
    """chunks_fts(FTS5 external content)에서도 해당 행을 지운다."""
    db.execute(
        text("INSERT INTO chunks_fts(chunks_fts, rowid, content, heading_path) VALUES ('delete', :id, :content, :heading)"),
        {"id": chunk.id, "content": chunk.content, "heading": chunk.heading_path},
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def dense_search(
    db: Session, query_embedding: list[float], top_n: int = 20, category: str | None = None
) -> list[tuple[int, float]]:
    query = db.query(DocumentChunk.id, DocumentChunk.embedding_json)
    if category:
        query = query.filter(DocumentChunk.category == category)
    rows = query.all()
    scored = []
    for chunk_id, emb_json in rows:
        emb = json.loads(emb_json)
        scored.append((chunk_id, _cosine(query_embedding, emb)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


_WORD_RE = re.compile(r"[\w가-힣]+", re.UNICODE)
_SCRIPT_BOUNDARY_RE = re.compile(r"(?<=[a-zA-Z0-9])(?=[가-힣])|(?<=[가-힣])(?=[a-zA-Z0-9])")


def _build_fts_query(raw_query: str) -> str:
    """FTS5 특수문자를 피하려고 단어만 뽑아 OR 쿼리로 만든다."""
    spaced = _SCRIPT_BOUNDARY_RE.sub(" ", raw_query)
    terms = _WORD_RE.findall(spaced)
    if not terms:
        return ""
    return " OR ".join(f'"{t}"' for t in terms)


def sparse_search(
    db: Session, query: str, top_n: int = 20, category: str | None = None
) -> list[tuple[int, float]]:
    safe_query = _build_fts_query(query)
    if not safe_query:
        return []
    try:
        if category:
            sql = """
                SELECT rowid, bm25(chunks_fts) AS score
                FROM chunks_fts
                WHERE chunks_fts MATCH :q
                  AND rowid IN (SELECT id FROM document_chunks WHERE category = :cat)
                ORDER BY score
                LIMIT :n
            """
            params = {"q": safe_query, "n": top_n, "cat": category}
        else:
            sql = """
                SELECT rowid, bm25(chunks_fts) AS score
                FROM chunks_fts
                WHERE chunks_fts MATCH :q
                ORDER BY score
                LIMIT :n
            """
            params = {"q": safe_query, "n": top_n}
        rows = db.execute(text(sql), params).fetchall()
    except Exception:
        return []
    return [(r[0], -r[1]) for r in rows]


def rrf_fuse(*ranked_lists: list[tuple[int, float]], k: int = 60) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, (chunk_id, _score) in enumerate(ranked):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def hybrid_search(
    db: Session, query: str, query_embedding: list[float], top_k: int, category: str | None = None
) -> list[tuple[DocumentChunk, float, float]]:
    """RRF 점수는 청크 선택용, 코사인 유사도는 답변 가능 여부 판단용이다."""
    dense = dense_search(db, query_embedding, category=category)
    dense_score_by_id = dict(dense)
    sparse = sparse_search(db, query, category=category)
    fused = rrf_fuse(dense, sparse)[:top_k]

    result = []
    for chunk_id, fused_score in fused:
        chunk = db.query(DocumentChunk).filter(DocumentChunk.id == chunk_id).first()
        if chunk:
            result.append((chunk, fused_score, dense_score_by_id.get(chunk_id, 0.0)))
    return result
