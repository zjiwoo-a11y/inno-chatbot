import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.database import SessionLocal, Base, engine
from app.models import DocumentChunk
from app.rag.chunking import chunk_pages, chunk_docx_paragraphs, strip_repeated_lines
from app.rag.embeddings import get_embedder, Embedder
from app.rag.search import ensure_fts_table, index_chunk_for_fts

SUPPORTED_EXTS = ("*.pdf", "*.docx")
ALLOWED_UPLOAD_SUFFIXES = (".pdf", ".docx")


def _extract_chunks(path: Path):
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        pages = strip_repeated_lines(pages)
        return chunk_pages(path.stem, pages)

    if suffix == ".docx":
        import docx
        document = docx.Document(str(path))
        paragraphs: list[tuple[int | None, str]] = []
        for p in document.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name or "").lower()
            level = None
            for n in (1, 2, 3):
                if style in (f"heading {n}", f"제목 {n}"):
                    level = n
                    break
            paragraphs.append((level, text))
        for table in document.tables:
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text:
                    paragraphs.append((None, row_text))
        return chunk_docx_paragraphs(path.stem, paragraphs)

    if suffix == ".doc":
        return []

    return []


def ingest_single_file(path: Path, db, embedder: Embedder, category: str = "") -> int:
    chunks = _extract_chunks(path)
    if not chunks:
        return 0

    embeddings = embedder.embed_batch([c.content for c in chunks])

    for chunk, emb in zip(chunks, embeddings):
        row = DocumentChunk(
            source_path=str(path),
            heading_path=chunk.heading_path,
            content=chunk.content,
            embedding_json=json.dumps(emb),
            category=category.strip(),
        )
        db.add(row)
        db.flush()
        index_chunk_for_fts(db, row)

    db.commit()
    return len(chunks)


def ingest_folder(folder: str = "docs", category: str = ""):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    ensure_fts_table(db)
    embedder = get_embedder()

    files = []
    for pattern in SUPPORTED_EXTS:
        files.extend(Path(folder).glob(f"**/{pattern}"))

    legacy_doc_files = list(Path(folder).glob("**/*.doc"))
    for old_doc in legacy_doc_files:
        print(f"  ! {old_doc.name}: 구버전 .doc 형식은 지원하지 않습니다. "
              f"Word에서 열어 '.docx'로 저장한 뒤 다시 넣어주세요.")

    if not files:
        print(f"'{folder}' 폴더에 .pdf / .docx 파일이 없습니다.")
        return

    total_chunks = 0
    for path in files:
        chunk_count = ingest_single_file(path, db, embedder, category=category)
        if chunk_count == 0:
            print(f"  - {path.name}: 추출된 텍스트가 없어 건너뜀 (스캔 이미지 PDF일 수 있음)")
        else:
            print(f"  - {path.name}: {chunk_count}개 청크")
        total_chunks += chunk_count

    db.close()
    print(f"완료: 문서 {len(files)}개, 총 {total_chunks}개 청크를 색인했습니다.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "docs"
    cat = sys.argv[2] if len(sys.argv) > 2 else ""
    ingest_folder(target, category=cat)
