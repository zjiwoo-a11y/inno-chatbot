import re
from pathlib import Path

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db, run_light_migrations
from app.models import User, ChatMessage, UnansweredQuestion, DocumentChunk, SqlSource
from app.auth import hash_password, verify_password, get_current_user, require_user, require_admin
from app.rag.search import hybrid_search, ensure_fts_table, remove_chunk_from_fts
from app.rag.embeddings import get_embedder
from app.rag.llm import get_llm_client, extract_citations
from app.rag.ingest import ingest_single_file, ALLOWED_UPLOAD_SUFFIXES
from app.sql.generator import generate_sql
from app.sql import loader as sql_loader
from app.sql import sources as sql_sources

DOCS_DIR = Path("docs")

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="프로젝트 수행 지원 RAG 챗봇")
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.exception_handler(StarletteHTTPException)
async def redirect_to_login(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/login", status_code=303)
    return await http_exception_handler(request, exc)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    run_light_migrations()
    db = next(get_db())
    ensure_fts_table(db)
    db.close()


USERNAME_PATTERN = re.compile(r"[A-Za-z0-9가-힣_-]{2,20}")
MIN_PASSWORD_LENGTH = 4


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register")
def register(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    username = username.strip()

    def fail(message: str):
        return templates.TemplateResponse("register.html", {"request": request, "error": message})

    if not USERNAME_PATTERN.fullmatch(username):
        return fail("아이디는 2~20자의 영문, 숫자, 한글, _, - 만 사용할 수 있습니다.")
    if len(password) < MIN_PASSWORD_LENGTH:
        return fail(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.")
    if db.query(User).filter(User.username == username).first():
        return fail("이미 사용 중인 아이디입니다.")
    role = "admin" if username == settings.FIRST_ADMIN_ID else "user"
    user = User(username=username, hashed_password=hash_password(password), role=role)
    db.add(user)
    db.commit()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username.strip(), User.is_active == True).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "아이디 또는 비밀번호가 올바르지 않습니다."}
        )
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _list_categories(db: Session) -> list[str]:
    rows = (
        db.query(DocumentChunk.category)
        .filter(DocumentChunk.category.isnot(None), DocumentChunk.category != "")
        .distinct()
        .order_by(DocumentChunk.category)
        .all()
    )
    return [r[0] for r in rows]


def _format_source(heading_path: str) -> str:
    parts = [p.strip() for p in heading_path.split(">") if p.strip()]
    if not parts:
        return heading_path
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} — {' > '.join(parts[1:])}"


@app.get("/", response_class=HTMLResponse)
def chat_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    has_documents = db.query(DocumentChunk.id).first() is not None
    return templates.TemplateResponse(
        "chat.html",
        {"request": request, "user": user, "has_documents": has_documents, "categories": _list_categories(db)},
    )


@app.post("/api/chat")
def chat_api(
    request: Request,
    question: str = Form(...),
    category: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    category = category.strip() or None

    def event_stream():
        try:
            if db.query(DocumentChunk.id).first() is None:
                yield "아직 업로드된 문서가 없습니다. 상단 메뉴의 '문서 업로드'에서 PDF 또는 Word 파일을 먼저 올려주세요."
                return

            embedder = get_embedder()
            query_embedding = embedder.embed(question)

            results = hybrid_search(db, question, query_embedding, top_k=settings.TOP_K, category=category)

            top_score = max((dense_score for _, _fused, dense_score in results), default=0.0)
            # 답변 가능 여부는 RRF 점수가 아니라 코사인 유사도로 판단한다
            if not results or top_score < settings.MIN_SCORE_THRESHOLD:
                answer = "죄송합니다. 제공된 문서에서 답을 찾을 수 없습니다. 담당자가 문서를 보완한 뒤 다시 답변드릴게요."
                _save_turn(db, user, question, answer, cited_chunk_ids=[], answerable=False)
                yield answer
                return

            citation_meta = [{"chunk_id": c.id, "heading_path": c.heading_path} for c, _fused, _dense in results]
            context_blocks = [f"({c.heading_path})\n{c.content}" for c, _fused, _dense in results]
            llm = get_llm_client()

            # [CITATIONS: ...] 태그가 화면에 보이지 않도록 끝 40자는 스트림이 끝날 때까지 보류
            HOLD_BACK = 40
            full_answer = ""
            hold = ""
            for token in llm.generate_stream(question, context_blocks):
                full_answer += token
                hold += token
                if len(hold) > HOLD_BACK:
                    emit = hold[:-HOLD_BACK]
                    yield emit
                    hold = hold[-HOLD_BACK:]

            body, cited_indices = extract_citations(full_answer)
            already_sent = len(full_answer) - len(hold)
            remaining = body[already_sent:] if already_sent < len(body) else ""
            if remaining:
                yield remaining

            valid_indices = [i for i in cited_indices if 1 <= i <= len(citation_meta)]
            real_chunk_ids = [citation_meta[i - 1]["chunk_id"] for i in valid_indices]
            answerable = len(valid_indices) > 0

            if answerable:
                seen_sources = []
                for i in valid_indices:
                    hp = citation_meta[i - 1]["heading_path"]
                    formatted = _format_source(hp)
                    if formatted not in seen_sources:
                        seen_sources.append(formatted)
                if seen_sources:
                    source_block = "\n".join(f"- {s}" for s in seen_sources)
                    yield f"\n\n---\n참고 문서\n{source_block}"

            _save_turn(db, user, question, body, real_chunk_ids, answerable, top_score)
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"\n\n[서버 오류] {type(e).__name__}: {e}"

    return StreamingResponse(event_stream(), media_type="text/plain")


def _save_turn(db: Session, user: User, question: str, answer: str,
               cited_chunk_ids: list[int], answerable: bool, top_score: float = 0.0):
    msg = ChatMessage(
        user_id=user.id,
        question=question,
        answer=answer,
        cited_chunk_ids=",".join(str(i) for i in cited_chunk_ids),
        was_answerable=answerable,
    )
    db.add(msg)
    if not answerable:
        db.add(UnansweredQuestion(user_id=user.id, question=question, best_score=str(round(top_score, 4))))
    db.commit()


@app.post("/api/sql")
def sql_api(
    question: str = Form(...),
    system: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    question = question.strip()
    if not question:
        return JSONResponse({"ok": False, "message": "질문을 입력해 주세요."}, status_code=400)
    try:
        result = generate_sql(question, system.strip() or None)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "message": f"{type(e).__name__}: {e}"}, status_code=500)

    tag = f"[SQL:{system.strip()}]" if system.strip() else "[SQL]"
    saved_answer = result.sql if result.ok else f"(생성 불가) {result.message}"
    db.add(ChatMessage(user_id=user.id, question=f"{tag} {question}", answer=saved_answer,
                       cited_chunk_ids="", was_answerable=result.ok))
    db.commit()
    return {"ok": result.ok, "sql": result.sql, "message": result.message, "warnings": result.warnings}


@app.get("/api/sql/systems")
def sql_systems(user: User = Depends(require_user)):
    out = []
    for sid in sql_loader.list_systems():
        try:
            name = sql_loader.load_metadata(sid).get("system_name") or sid
        except Exception:
            name = sid
        out.append({"id": sid, "name": name})
    return out


@app.get("/admin/sql-metadata", response_class=HTMLResponse)
def sql_metadata_page(request: Request, msg: str = "", error: str = "", admin: User = Depends(require_admin)):
    systems = []
    for sid in sql_loader.list_systems():
        source = sql_loader.build_source(sid)
        item = {"id": sid, "status": sql_loader.status(sid), "summary": None, "load_error": "",
                "is_google": source.kind == "google", "sheet_url": getattr(source, "edit_url", "")}
        try:
            meta = sql_loader.load_metadata(sid)
            item["name"] = meta.get("system_name") or sid
            item["summary"] = {
                "tables": [(t["name"], t.get("korean_name", ""), len(t["columns"])) for t in meta["tables"]],
                "glossary": len(meta["glossary"]), "examples": len(meta["examples"]), "rules": len(meta["global_rules"]),
            }
            item["status"] = sql_loader.status(sid)
        except Exception as e:
            item["name"] = sid
            item["load_error"] = str(e)
        systems.append(item)
    return templates.TemplateResponse(
        "admin_sql_metadata.html",
        {"request": request, "user": admin, "systems": systems, "msg": msg, "error": error,
         "default_system": settings.SQL_DEFAULT_SYSTEM},
    )


@app.get("/admin/sql-metadata/download")
def sql_metadata_download(system: str = "", admin: User = Depends(require_admin)):
    from fastapi.responses import FileResponse
    sid = sql_loader.resolve_system(system or None)
    source = sql_loader.build_source(sid)
    if source.kind == "google":
        return RedirectResponse(url=source.export_url, status_code=303)
    return FileResponse(sql_loader.local_path(sid), filename=f"{sid}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/admin/sql-metadata/source")
async def sql_metadata_source(
    system: str = Form(""),
    kind: str = Form("google"),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import datetime
    from urllib.parse import quote

    def back(**kw):
        key, value = next(iter(kw.items()))
        return RedirectResponse(url=f"/admin/sql-metadata?{key}=" + quote(value), status_code=303)

    try:
        if kind == "google":
            sheet_url = sheet_url.strip()
            if not sql_sources.sheet_id_of(sheet_url):
                raise ValueError("구글 시트 URL을 입력해 주세요.")
            raw = sql_sources.GoogleSheetSource(sheet_url, settings.SQL_METADATA_TTL).read()
        else:
            if file is None or not file.filename:
                raise ValueError("엑셀 파일을 선택해 주세요.")
            raw = await file.read()
        parsed = sql_loader.parse_workbook(raw)
        sid = system.strip() or parsed.get("system_id") or settings.SQL_DEFAULT_SYSTEM
        path = sql_sources.local_path(sid)
    except (ValueError, RuntimeError, OSError) as e:
        return back(error=str(e))

    if kind != "google":
        if path.exists():
            path.with_suffix(".xlsx.bak").write_bytes(path.read_bytes())
        path.write_bytes(raw)
    row = db.get(SqlSource, sid) or SqlSource(system_id=sid)
    row.kind = "google" if kind == "google" else "excel"
    row.sheet_url = sheet_url if kind == "google" else ""
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    sql_loader.reload_metadata(sid)
    return back(msg=f"'{sid}' 원본을 {'구글 시트' if kind == 'google' else '엑셀 파일'}로 설정했습니다.")


@app.post("/admin/sql-metadata/reload")
def sql_metadata_reload(system: str = Form(""), admin: User = Depends(require_admin)):
    from urllib.parse import quote
    try:
        sql_loader.reload_metadata(system or None)
    except Exception as e:
        return RedirectResponse(url="/admin/sql-metadata?error=" + quote(str(e)), status_code=303)
    return RedirectResponse(url="/admin/sql-metadata?msg=" + quote("다시 불러왔습니다."), status_code=303)


@app.get("/admin/history", response_class=HTMLResponse)
def admin_history(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    messages = db.query(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(200).all()
    return templates.TemplateResponse("admin_history.html", {"request": request, "messages": messages, "user": admin})


@app.get("/admin/unanswered", response_class=HTMLResponse)
def admin_unanswered(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    items = db.query(UnansweredQuestion).filter(UnansweredQuestion.status == "open") \
        .order_by(UnansweredQuestion.created_at.desc()).all()
    note_required_error = request.query_params.get("error") == "note_required"
    return templates.TemplateResponse(
        "admin_unanswered.html",
        {"request": request, "items": items, "user": admin, "note_required_error": note_required_error},
    )


@app.post("/admin/unanswered/{item_id}/resolve")
def resolve_unanswered(
    item_id: int, note: str = Form(""),
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    # 브라우저 required를 우회할 수 있으므로 서버에서도 확인
    if not note.strip():
        return RedirectResponse(url=f"/admin/unanswered?error=note_required#item-{item_id}", status_code=303)

    item = db.query(UnansweredQuestion).filter(UnansweredQuestion.id == item_id).first()
    if item:
        item.status = "resolved"
        item.resolution_note = note.strip()
        db.commit()
    return RedirectResponse(url="/admin/unanswered", status_code=303)


def _list_uploaded_documents(db: Session):
    rows = (
        db.query(DocumentChunk.source_path, DocumentChunk.category, func.count(DocumentChunk.id))
        .group_by(DocumentChunk.source_path, DocumentChunk.category)
        .order_by(DocumentChunk.category, DocumentChunk.source_path)
        .all()
    )
    return [
        {
            "source_path": source_path,
            "filename": Path(source_path).name,
            "category": category or "",
            "chunk_count": count,
        }
        for source_path, category, count in rows
    ]


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "upload.html",
        {
            "request": request, "user": user, "results": None,
            "documents": _list_uploaded_documents(db),
            "categories": _list_categories(db),
        },
    )


@app.post("/upload", response_class=HTMLResponse)
def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    category: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    DOCS_DIR.mkdir(exist_ok=True)
    ensure_fts_table(db)
    embedder = get_embedder()
    category = category.strip()

    results = []
    for f in files:
        filename = Path(f.filename or "").name
        if not filename:
            continue
        suffix = Path(filename).suffix.lower()

        if suffix == ".doc":
            results.append({"filename": filename, "chunks": 0,
                             "message": "구버전 .doc 형식은 지원하지 않습니다. Word에서 '.docx'로 저장한 뒤 다시 올려주세요."})
            continue
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            results.append({"filename": filename, "chunks": 0,
                             "message": f"지원하지 않는 형식입니다 ({suffix}). .pdf/.docx만 가능합니다."})
            continue

        dest = DOCS_DIR / filename
        dest.write_bytes(f.file.read())

        try:
            chunk_count = ingest_single_file(dest, db, embedder, category=category)
        except Exception as e:
            results.append({"filename": filename, "chunks": 0, "message": f"오류: {e}"})
            continue

        if chunk_count == 0:
            results.append({"filename": filename, "chunks": 0,
                             "message": "텍스트를 추출하지 못했습니다 (스캔 이미지 PDF일 수 있음)"})
        else:
            results.append({"filename": filename, "chunks": chunk_count, "message": ""})

    return templates.TemplateResponse(
        "upload.html",
        {
            "request": request, "user": user, "results": results,
            "documents": _list_uploaded_documents(db),
            "categories": _list_categories(db),
        },
    )


@app.post("/upload/delete")
def delete_document(
    source_path: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    chunks = db.query(DocumentChunk).filter(DocumentChunk.source_path == source_path).all()
    for chunk in chunks:
        remove_chunk_from_fts(db, chunk)
        db.delete(chunk)
    db.commit()

    file_path = Path(source_path)
    if file_path.exists():
        try:
            file_path.unlink()
        except OSError:
            pass

    return RedirectResponse(url="/upload", status_code=303)
