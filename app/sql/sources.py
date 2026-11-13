import re
import time
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import settings

METADATA_DIR = Path(__file__).parent / "metadata"


class MetadataSource(ABC):
    """메타데이터 원본 인터페이스. 새 원본은 이 클래스를 상속해 register_source로 등록한다."""
    kind = "abstract"

    @abstractmethod
    def fingerprint(self):
        pass

    @abstractmethod
    def read(self) -> bytes: ...

    @abstractmethod
    def describe(self) -> str: ...


class LocalExcelSource(MetadataSource):
    kind = "excel"

    def __init__(self, path: Path):
        self.path = path

    def fingerprint(self):
        return self.path.stat().st_mtime

    def read(self) -> bytes:
        return self.path.read_bytes()

    def describe(self) -> str:
        return f"로컬 엑셀 ({self.path.name})"


def sheet_id_of(src: str) -> str | None:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", src)
    if m:
        return m.group(1)
    return src if re.fullmatch(r"[A-Za-z0-9_-]{20,}", src) else None


class GoogleSheetSource(MetadataSource):
    kind = "google"

    def __init__(self, src: str, ttl: int):
        self.sheet_id = sheet_id_of(src)
        self.ttl = max(ttl, 1)

    def fingerprint(self):
        return int(time.time() // self.ttl)

    @property
    def edit_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit"

    @property
    def export_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export?format=xlsx"

    def read(self) -> bytes:
        url = self.export_url
        with urllib.request.urlopen(url, timeout=15) as r:
            data = r.read()
        if not data.startswith(b"PK"):
            raise RuntimeError("구글 시트를 읽지 못했습니다. 공유 설정을 '링크가 있는 모든 사용자: 뷰어'로 바꿔 주세요.")
        return data

    def describe(self) -> str:
        return f"구글 시트 ({self.sheet_id})"


SOURCE_KINDS: dict[str, type[MetadataSource]] = {"excel": LocalExcelSource, "google": GoogleSheetSource}


def register_source(kind: str, cls: type[MetadataSource]) -> None:
    SOURCE_KINDS[kind] = cls


def _configured_remote() -> dict[str, str]:
    out: dict[str, str] = {}
    for part in settings.SQL_METADATA_SOURCES.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    legacy = settings.SQL_METADATA_SOURCE.strip()
    if legacy and settings.SQL_DEFAULT_SYSTEM not in out:
        out[settings.SQL_DEFAULT_SYSTEM] = legacy
    return out


def stored_sources() -> dict[str, tuple[str, str]]:
    """화면에서 저장한 원본 설정 {시스템ID: (종류, 시트URL)}. 테이블이 아직 없으면 빈 값."""
    try:
        from app.database import SessionLocal
        from app.models import SqlSource
        with SessionLocal() as db:
            return {r.system_id: (r.kind, r.sheet_url or "") for r in db.query(SqlSource).all()}
    except Exception:
        return {}


def list_systems() -> list[str]:
    ids = {p.stem for p in METADATA_DIR.glob("*.xlsx")}
    ids |= set(_configured_remote())
    ids |= set(stored_sources())
    ids.discard("")
    default = settings.SQL_DEFAULT_SYSTEM
    return sorted(ids, key=lambda s: (s != default, s))


def local_path(system: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", system):
        raise ValueError("시스템 ID는 영문/숫자/_/- 만 사용할 수 있습니다.")
    return METADATA_DIR / f"{system}.xlsx"


def build_source(system: str) -> MetadataSource:
    stored = stored_sources().get(system)
    if stored:
        kind, url = stored
        if kind == "google" and sheet_id_of(url):
            return GoogleSheetSource(url, settings.SQL_METADATA_TTL)
        if kind == "excel":
            return LocalExcelSource(local_path(system))
    remote = _configured_remote().get(system)
    if remote and sheet_id_of(remote):
        return GoogleSheetSource(remote, settings.SQL_METADATA_TTL)
    return LocalExcelSource(local_path(system))
