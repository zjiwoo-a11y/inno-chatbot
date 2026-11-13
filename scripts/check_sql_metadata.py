"""메타데이터의 예시 SQL이 테이블/컬럼 정의와 맞는지 검사한다. 사용법: python scripts/check_sql_metadata.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sql.loader import load_examples, load_metadata, build_messages, status
from app.sql.generator import validate_sql

meta = load_metadata()
print("원본:", status()["source"])
print(f"테이블 {len(meta['tables'])}개: {', '.join(t['name'] for t in meta['tables'])}")
examples = load_examples()
print(f"예시 {len(examples)}개 검사 중...")
bad = 0
for e in examples:
    ok, warnings = validate_sql(e["sql"], meta)
    if not ok or warnings:
        bad += 1
        print(f"  {e['question']}\n     {warnings}")
print("결과:", "모두 통과" if bad == 0 else f"{bad}개 문제 있음")
print(f"프롬프트(system) 길이: {len(build_messages('테스트')[0]['content'])}자")

if len(sys.argv) > 1:
    for message in build_messages(" ".join(sys.argv[1:])):
        print(f"\n[{message['role']}]\n{message['content']}")
