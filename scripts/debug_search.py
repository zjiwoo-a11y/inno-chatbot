"""검색 결과 확인용. 사용법: python scripts/debug_search.py "질문" [--full] [--category=이름]"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.rag.embeddings import get_embedder
from app.rag.search import hybrid_search
from app.config import settings


def main(question: str, show_full: bool = False, category: str | None = None):
    db = SessionLocal()
    embedder = get_embedder()
    query_embedding = embedder.embed(question)

    results = hybrid_search(db, question, query_embedding, top_k=settings.TOP_K, category=category)

    print(f"\n질문: {question}")
    if category:
        print(f"카테고리 제한: {category}")
    print(f"현재 MIN_SCORE_THRESHOLD = {settings.MIN_SCORE_THRESHOLD}\n")

    if not results:
        print("!! 검색 결과가 아예 없습니다. document_chunks 테이블이 비어있거나, 지정한 카테고리에 문서가 없을 수 있습니다.")
        print("   python -m app.rag.ingest docs 를 실행했는지 확인하세요.")
        return

    for i, (chunk, fused_score, dense_score) in enumerate(results, start=1):
        mark = "OK " if dense_score >= settings.MIN_SCORE_THRESHOLD else "낮음"
        cat_label = f" [{chunk.category}]" if chunk.category else ""
        print(f"[{i}] 코사인유사도={dense_score:.4f} ({mark})  RRF점수={fused_score:.4f}{cat_label}")
        print(f"    출처: {chunk.heading_path}")
        if show_full:
            print(f"    내용:\n{chunk.content}\n")
        else:
            preview = chunk.content[:150].replace("\n", " ")
            print(f"    내용: {preview}...\n")

    top = max(d for _, _, d in results)
    if top < settings.MIN_SCORE_THRESHOLD:
        print(f"=> 최고 점수({top:.4f})가 임계값({settings.MIN_SCORE_THRESHOLD})보다 낮아 '근거 부족'으로 처리됩니다.")
    else:
        print(f"=> 최고 점수({top:.4f})가 임계값을 넘어서 정상적으로 답변을 생성해야 합니다.")
        print("   (그런데도 GPT가 '답을 찾을 수 없다'고 하면, 위 청크 '내용'에 실제 설명 문장이")
        print("    들어있는지 --full 옵션으로 확인하세요. 제목/그림 캡션만 있으면 GPT도 답을 못 만듭니다.)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용법: python scripts/debug_search.py "질문 내용" [--full] [--category=카테고리명]')
        sys.exit(1)
    full_flag = "--full" in sys.argv[2:]
    cat_arg = None
    for arg in sys.argv[2:]:
        if arg.startswith("--category="):
            cat_arg = arg.split("=", 1)[1]
    main(sys.argv[1], show_full=full_flag, category=cat_arg)
