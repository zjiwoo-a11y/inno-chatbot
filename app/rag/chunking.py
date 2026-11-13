import re
from dataclasses import dataclass


@dataclass
class Chunk:
    heading_path: str
    content: str


MAX_CHUNK_CHARS = 1200


def strip_repeated_lines(pages: list[str], min_ratio: float = 0.12) -> list[str]:
    """여러 페이지에 반복되는 머리말/꼬리말 조각을 제거한다."""
    from collections import Counter

    SPLIT_RE = re.compile(r"\s{2,}")

    pages_lines = [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages]
    pages_segments = [[[s.strip() for s in SPLIT_RE.split(ln) if s.strip()] for ln in lines]
                       for lines in pages_lines]

    counter: Counter[str] = Counter()
    for page_segs in pages_segments:
        seen_this_page = set()
        for segs in page_segs:
            for s in segs:
                norm = re.sub(r"\d+", "#", s)
                if norm not in seen_this_page:
                    counter[norm] += 1
                    seen_this_page.add(norm)

    non_empty_pages = max(1, sum(1 for lines in pages_lines if lines))
    threshold = max(2, int(non_empty_pages * min_ratio))

    cleaned_pages: list[str] = []
    for page_segs in pages_segments:
        kept_lines = []
        for segs in page_segs:
            kept = []
            for s in segs:
                if s.isdigit():
                    continue
                norm = re.sub(r"\d+", "#", s)
                if counter[norm] >= threshold:
                    continue
                kept.append(s)
            merged = " ".join(kept).strip()
            if merged:
                kept_lines.append(merged)
        cleaned_pages.append("\n".join(kept_lines))
    return cleaned_pages


def _chunk_by_headings(doc_name: str, lines: list[tuple[int | None, str]]) -> list[Chunk]:
    stack: list[str] = []
    chunks: list[Chunk] = []
    buffer: list[str] = []

    def flush():
        content = "\n".join(buffer).strip()
        if content:
            path = " > ".join([doc_name] + [s for s in stack if s])
            chunks.extend(_split_long(path, content))
        buffer.clear()

    for level, text in lines:
        if level is not None:
            flush()
            stack[:] = stack[: level - 1]
            while len(stack) < level:
                stack.append("")
            stack[level - 1] = text.strip()
        else:
            buffer.append(text)

    flush()
    return chunks


def chunk_docx_paragraphs(doc_name: str, paragraphs: list[tuple[int | None, str]]) -> list[Chunk]:
    return _chunk_by_headings(doc_name, paragraphs)


def _split_long(path: str, content: str) -> list[Chunk]:
    if len(content) <= MAX_CHUNK_CHARS:
        return [Chunk(heading_path=path, content=content)]

    paragraphs = content.split("\n\n")
    out: list[Chunk] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 > MAX_CHUNK_CHARS and buf:
            out.append(Chunk(heading_path=path, content=buf.strip()))
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf.strip():
        out.append(Chunk(heading_path=path, content=buf.strip()))
    return out


def chunk_pages(doc_name: str, pages: list[str], overlap_chars: int = 300) -> list[Chunk]:
    """페이지 단위로 자르고, 앞 페이지 끝부분을 겹쳐 붙인다."""
    chunks: list[Chunk] = []
    for i, page_text in enumerate(pages):
        page_text = page_text.strip()
        if not page_text:
            continue

        prefix = ""
        if i > 0:
            prev_text = pages[i - 1].strip()
            if prev_text:
                prefix = prev_text[-overlap_chars:] + "\n"

        page_no = i + 1
        path = f"{doc_name} > {page_no}페이지"
        chunks.extend(_split_long(path, f"{prefix}{page_text}"))
    return chunks
