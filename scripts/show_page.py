"""저장된 청크 내용 확인용. 사용법: python scripts/show_page.py 186"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import DocumentChunk


def main(keyword: str):
    db = SessionLocal()
    rows = db.query(DocumentChunk).filter(DocumentChunk.heading_path.contains(keyword)).all()

    if not rows:
        print(f"'{keyword}'가 포함된 heading_path를 가진 청크가 없습니다.")
        print("(재색인을 안 했거나, 그 페이지에서 추출된 텍스트가 아예 없었을 수 있습니다.)")
        return

    print(f"'{keyword}' 관련 청크 {len(rows)}개:\n")
    for row in rows:
        print(f"[id={row.id}] 출처: {row.heading_path}")
        print(f"내용:\n{row.content}")
        print("-" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용법: python scripts/show_page.py "186" 또는 "DB 룰"')
        sys.exit(1)
    main(sys.argv[1])
