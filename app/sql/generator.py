import re
from dataclasses import dataclass, field

from app.config import settings
from app.sql.loader import build_messages, load_metadata, table_names, column_names

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC|EXECUTE|CALL)\b", re.I
)


@dataclass
class SqlResult:
    sql: str = ""
    ok: bool = False
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def clean_sql(raw: str) -> str:
    """코드펜스와 끝 세미콜론을 제거한다."""
    text = raw.strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    text = re.sub(r"^\s*SQL\s*:\s*", "", text, flags=re.I)
    return text.strip().rstrip(";").strip()


def _strip_literals_and_comments(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    return re.sub(r"'(?:[^']|'')*'", "''", s)


def validate_sql(sql: str, meta: dict | None = None, system: str | None = None) -> tuple[bool, list[str]]:
    """SELECT 전용 여부와 메타데이터의 테이블/컬럼 일치 여부를 검사한다."""
    meta = meta or load_metadata(system)
    warnings: list[str] = []
    body = _strip_literals_and_comments(sql)

    if not re.match(r"^\s*(SELECT|WITH)\b", body, re.I):
        return False, ["SELECT 문이 아닙니다."]
    if ";" in body:
        return False, ["여러 문장이 포함되어 있습니다."]
    m = _FORBIDDEN.search(body)
    if m:
        return False, [f"허용되지 않는 키워드({m.group(1).upper()})가 포함되어 있습니다."]

    known = table_names(meta)
    cols = column_names(meta)
    aliases: dict[str, str] = {}
    for tbl, alias in re.findall(r"\b(?:FROM|JOIN|,)\s*([A-Za-z_][\w$#]*)\s+(?:AS\s+)?([A-Za-z_]\w*)?", body, re.I):
        t = tbl.upper()
        if t in known:
            aliases[(alias or tbl).upper()] = t
    used_tables = {t.upper() for t in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$#]*)", body, re.I)}
    used_tables |= {t.upper() for t in re.findall(r",\s*([A-Za-z_][\w$#]*)\s+[A-Za-z_]\w*\s*(?:,|WHERE|$|\n)", body, re.I) if t.upper() in known}
    for t in sorted(used_tables):
        if t not in known and t not in ("DUAL", "SELECT"):
            warnings.append(f"메타데이터에 없는 테이블 사용: {t}")

    seen = set()
    for alias, col in re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", body):
        a, c = alias.upper(), col.upper()
        tbl = aliases.get(a) or (a if a in known else None)
        if tbl and c not in cols[tbl] and (tbl, c) not in seen:
            seen.add((tbl, c))
            warnings.append(f"{tbl} 테이블에 없는 컬럼: {c}")
    return True, warnings


def generate_sql(question: str, system: str | None = None) -> SqlResult:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    messages = build_messages(question, system)
    last_error = None
    raw = None
    for model in (settings.OPENAI_CHAT_MODEL, settings.OPENAI_FALLBACK_MODEL):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, temperature=0)
            raw = resp.choices[0].message.content or ""
            break
        except Exception as e:
            last_error = e
    if raw is None:
        raise RuntimeError(f"모든 OpenAI 모델 호출 실패: {last_error}")
    return postprocess(raw, system)


def postprocess(raw: str, system: str | None = None) -> SqlResult:
    text = raw.strip()
    m = re.match(r"^CANNOT_GENERATE\s*:\s*(.*)$", text, re.S)
    if m:
        return SqlResult(ok=False, message=m.group(1).strip() or "SQL을 생성할 수 없는 질문입니다.")
    sql = clean_sql(text)
    ok, warnings = validate_sql(sql, system=system)
    if not ok:
        return SqlResult(sql=sql, ok=False, message=warnings[0], warnings=warnings)
    return SqlResult(sql=sql, ok=True, warnings=warnings)
