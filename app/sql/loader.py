import io
import json
import re
import time

from app.config import settings
from app.sql.sources import (
    MetadataSource, build_source, list_systems, local_path, register_source,
)

REQUIRED_SHEETS = ["전역규칙", "테이블", "컬럼", "관계", "용어집", "예시", "프롬프트"]
SUPPORTED_SCHEMA_VERSIONS = {"1"}

_cache: dict[str, dict] = {}


def _cell(v) -> str:
    return "" if v is None else str(v).strip()


def _norm(h: str) -> str:
    return re.split(r"[(（]", h)[0].strip()


def parse_workbook(xlsx_bytes: bytes) -> dict:
    """xlsx를 메타데이터 dict로 변환한다. 구조가 틀리면 ValueError."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    except Exception as e:
        raise ValueError(f"엑셀 파일을 열 수 없습니다: {e}")
    missing = [n for n in REQUIRED_SHEETS if n not in wb.sheetnames]
    if missing:
        raise ValueError(f"필수 시트가 없습니다: {', '.join(missing)}")

    def table(name: str, known: set[str]):
        it = wb[name].iter_rows(values_only=True)
        header = next(it, None)
        if header is None:
            return []
        keys = [_norm(_cell(h)) for h in header]
        out = []
        for row in it:
            cells = [_cell(c) for c in row]
            if not any(cells):
                continue
            d = {"_extra": {}, "_first": cells[0] if cells else ""}
            for k, v in zip(keys, cells):
                if not k:
                    continue
                if k in known:
                    d[k] = v
                elif v:
                    d["_extra"][k] = v
            out.append(d)
        return out

    def need(name: str, rows: list[dict], cols: list[str]):
        if rows is not None:
            hdr = set(rows[0].keys()) if rows else set()
            lack = [c for c in cols if rows and c not in hdr]
            if lack:
                raise ValueError(f"'{name}' 시트에 필요한 머리글이 없습니다: {', '.join(lack)}")

    meta: dict = {"dialect": "Oracle", "system_id": "", "system_name": "", "schema_version": "1"}
    for r in table("안내", {"항목", "내용"}) if "안내" in wb.sheetnames else []:
        k, v = r.get("항목", ""), r.get("내용", "")
        if k == "DB 방언" and v:
            meta["dialect"] = v
        elif k == "시스템ID":
            meta["system_id"] = v
        elif k == "시스템명":
            meta["system_name"] = v
        elif k == "스키마버전" and v:
            meta["schema_version"] = v
        elif k == "버전":
            meta["version"] = v
    if meta["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"지원하지 않는 스키마버전입니다: {meta['schema_version']} (지원: {', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))})")

    meta["global_rules"] = [r["_first"] for r in table("전역규칙", set()) if r["_first"]]

    trows = table("테이블", {"테이블명", "한글명", "역할", "설명", "PK"})
    need("테이블", trows, ["테이블명"])
    tables: dict[str, dict] = {}
    for r in trows:
        if not r.get("테이블명"):
            continue
        tables[r["테이블명"].upper()] = {
            "name": r["테이블명"], "korean_name": r.get("한글명", ""), "role": r.get("역할") or "core",
            "description": r.get("설명", ""),
            "primary_key": [x.strip() for x in r.get("PK", "").split(",") if x.strip()],
            "extra": r["_extra"], "columns": [],
        }
    if not tables:
        raise ValueError("'테이블' 시트에 테이블이 1개 이상 있어야 합니다.")

    crows = table("컬럼", {"테이블명", "컬럼명", "타입", "설명", "값설명"})
    need("컬럼", crows, ["테이블명", "컬럼명"])
    for r in crows:
        if not r.get("테이블명") or not r.get("컬럼명"):
            continue
        t = tables.get(r["테이블명"].upper())
        if t is None:
            raise ValueError(f"'컬럼' 시트의 테이블 '{r['테이블명']}' 이(가) '테이블' 시트에 없습니다.")
        col = {"name": r["컬럼명"], "type": r.get("타입", ""), "description": r.get("설명", ""), "extra": r["_extra"]}
        vals = {}
        for part in r.get("값설명", "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                vals[k.strip()] = v.strip()
        if vals:
            col["values"] = vals
        t["columns"].append(col)
    empty = [t["name"] for t in tables.values() if not t["columns"]]
    if empty:
        raise ValueError(f"컬럼이 없는 테이블이 있습니다: {', '.join(empty)}")
    meta["tables"] = list(tables.values())

    meta["relationships"] = [
        {"from": r.get("FROM", ""), "to": r.get("TO", ""), "description": r.get("설명", "")}
        for r in table("관계", {"FROM", "TO", "설명"}) if r.get("FROM") and r.get("TO")
    ]
    meta["glossary"] = [
        {"term": r["용어"], "synonyms": [x.strip() for x in r.get("동의어", "").split(",") if x.strip()],
         "meaning": r.get("의미", ""), "sql_hint": r.get("SQL 힌트", ""), "extra": r["_extra"]}
        for r in table("용어집", {"용어", "동의어", "의미", "SQL 힌트"}) if r.get("용어")
    ]
    meta["examples"] = [
        {"question": r["질문"], "sql": r.get("SQL", "").rstrip(";").strip()}
        for r in table("예시", {"질문", "SQL"}) if r.get("질문") and r.get("SQL")
    ]
    meta["system_prompt"] = "\n".join(r["_first"] for r in table("프롬프트", set()) if r["_first"]).strip()
    if not meta["system_prompt"]:
        raise ValueError("'프롬프트' 시트가 비어 있습니다.")
    return meta


def resolve_system(system: str | None) -> str:
    system = (system or settings.SQL_DEFAULT_SYSTEM).strip()
    if system not in list_systems():
        raise ValueError(f"알 수 없는 시스템입니다: {system}")
    return system


def _load(system: str | None = None) -> dict:
    system = resolve_system(system)
    src = build_source(system)
    entry = _cache.get(system)
    if entry and entry["fp"] == src.fingerprint() and entry["data"] is not None:
        return entry["data"]
    try:
        fp = src.fingerprint()
        data = parse_workbook(src.read())
        _cache[system] = {"data": data, "fp": fp, "source": src.describe(), "error": ""}
    except Exception as e:
        if entry and entry["data"] is not None:
            entry.update(error=str(e), fp=src.fingerprint())
            return entry["data"]
        if src.kind != "excel":
            try:
                lp = local_path(system)
                if lp.exists():
                    data = parse_workbook(lp.read_bytes())
                    _cache[system] = {"data": data, "fp": None, "source": f"로컬 엑셀 ({lp.name}, 원격 실패로 대체)", "error": str(e)}
                    return data
            except Exception:
                pass
        raise
    return _cache[system]["data"]


def reload_metadata(system: str | None = None) -> dict:
    _cache.pop(resolve_system(system), None)
    return _load(system)


def status(system: str | None = None) -> dict:
    system = resolve_system(system)
    e = _cache.get(system)
    return {"source": e["source"] if e else build_source(system).describe(), "error": e["error"] if e else ""}


def load_metadata(system: str | None = None) -> dict:
    return _load(system)


def load_system_prompt(system: str | None = None) -> str:
    return _load(system)["system_prompt"]


def load_examples(system: str | None = None) -> list[dict]:
    return _load(system)["examples"]


def table_names(meta: dict) -> set[str]:
    return {t["name"].upper() for t in meta.get("tables", [])}


def column_names(meta: dict) -> dict[str, set[str]]:
    return {t["name"].upper(): {c["name"].upper() for c in t.get("columns", [])} for t in meta.get("tables", [])}


def _compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in ("", None, [], {})}


def metadata_to_json(meta: dict) -> dict:
    tables = []
    for t in meta.get("tables", []):
        columns = [_compact({**c.get("extra", {}), "name": c["name"], "type": c.get("type", ""),
                             "description": c.get("description", ""), "values": c.get("values", {})})
                   for c in t.get("columns", [])]
        tables.append(_compact({**t.get("extra", {}), "name": t["name"], "korean_name": t.get("korean_name", ""),
                                "role": t.get("role", ""), "description": t.get("description", ""),
                                "primary_key": t.get("primary_key", []), "columns": columns}))
    glossary = [_compact({**g.get("extra", {}), "term": g["term"], "synonyms": g.get("synonyms", []),
                          "meaning": g.get("meaning", ""), "sql_hint": g.get("sql_hint", "")})
                for g in meta.get("glossary", [])]
    return _compact({
        "system": meta.get("system_name", ""),
        "dialect": meta.get("dialect", "Oracle"),
        "global_rules": meta.get("global_rules", []),
        "tables": tables,
        "relationships": [_compact(r) for r in meta.get("relationships", [])],
        "glossary": glossary,
    })


def render_metadata(meta: dict) -> str:
    body = json.dumps(metadata_to_json(meta), ensure_ascii=False, separators=(",", ":"))
    return "<메타데이터>\n" + body + "\n</메타데이터>"


def _tokens(s: str) -> set[str]:
    toks: set[str] = set()
    for w in re.findall(r"[가-힣]{2,}|[A-Za-z0-9_#]{2,}", s.lower()):
        toks.add(w)
        if re.match(r"[가-힣]", w):
            toks.update(w[i:i + 2] for i in range(len(w) - 1))
    return toks


def select_examples(question: str, examples: list[dict], limit: int = 8) -> list[dict]:
    if len(examples) <= limit:
        return examples
    q = _tokens(question)
    scored = sorted(examples, key=lambda e: len(q & _tokens(e["question"] + " " + e["sql"])), reverse=True)
    return scored[:limit]


def render_examples(examples: list[dict]) -> str:
    if not examples:
        return ""
    items = [{"question": e["question"], "sql": e["sql"].splitlines()} for e in examples]
    return "<예시>\n" + json.dumps(items, ensure_ascii=False, separators=(",", ":")) + "\n</예시>"


def build_messages(question: str, system_id: str | None = None) -> list[dict]:
    meta = load_metadata(system_id)
    system = meta["system_prompt"] + "\n\n" + render_metadata(meta)
    shots = render_examples(select_examples(question, meta["examples"]))
    user = (shots + "\n\n" if shots else "") + f"질문: {question}\nSQL:"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
