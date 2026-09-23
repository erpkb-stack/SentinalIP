"""Evidence upload, review and retrieval.

Uploads are validated three ways: extension allow-list, declared MIME type,
and magic-byte sniffing of the actual content. Files are stored outside the
web root under a generated name; the original name is kept as metadata only.
"""
from __future__ import annotations

import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.cases import load_case, serialize_evidence
from app.auth import CsrfProtected, get_current_user, scope_to_vendor
from app.auth.security import sha256_bytes
from app.config import settings
from app.constants import (
    AuditAction,
    EvidenceCategory,
    EvidenceRelevance,
    EvidenceType,
    Permission,
)
from app.database import get_db, utcnow
from app.errors import NotFound, PermissionDenied, UploadRejected, ValidationFailed
from app.models import Case, Evidence, User
from app.schemas.cases import EvidenceOut, EvidenceReviewRequest, EvidenceUrlCreate
from app.schemas.common import Page
from app.services import audit
from app.services.references import next_evidence_ref

router = APIRouter(prefix="/api", tags=["evidence"])

#: extension -> (mime prefixes we accept, evidence type)
UPLOAD_KINDS = {
    ".png": (("image/png",), EvidenceType.IMAGE.value),
    ".jpg": (("image/jpeg",), EvidenceType.IMAGE.value),
    ".jpeg": (("image/jpeg",), EvidenceType.IMAGE.value),
    ".gif": (("image/gif",), EvidenceType.IMAGE.value),
    ".webp": (("image/webp",), EvidenceType.IMAGE.value),
    ".pdf": (("application/pdf",), EvidenceType.PDF.value),
    ".txt": (("text/plain",), EvidenceType.TEXT.value),
    ".csv": (("text/csv", "text/plain", "application/csv"), EvidenceType.CSV.value),
    ".xls": (("application/vnd.ms-excel",), EvidenceType.SPREADSHEET.value),
    ".xlsx": (
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
        EvidenceType.SPREADSHEET.value,
    ),
}


def _safe_extension(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in settings.allowed_upload_extensions or ext not in UPLOAD_KINDS:
        raise UploadRejected(
            f"'{ext or 'unknown'}' files are not accepted. Allowed: "
            + ", ".join(sorted(settings.allowed_upload_extensions))
        )
    return ext


def _sniff_ok(ext: str, content: bytes) -> bool:
    """Magic-byte check. Text formats have no signature, so they pass through."""
    if ext in (".txt", ".csv"):
        try:
            content[:4096].decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    try:
        import filetype
        kind = filetype.guess(content)
    except Exception:  # pragma: no cover - library optional at runtime
        return True
    if kind is None:
        return False
    accepted, _ = UPLOAD_KINDS[ext]
    # xlsx is a zip container; filetype reports it as zip or the office type.
    if ext == ".xlsx":
        return kind.mime in accepted or kind.mime == "application/zip"
    if ext == ".xls":
        return kind.mime in accepted or kind.mime == "application/x-ole-storage"
    return kind.mime in accepted


def _case_or_403(db: Session, case_id: int, user: User) -> Case:
    case = load_case(db, case_id, user)
    if not user.has_permission(Permission.EVIDENCE_UPLOAD.value):
        raise PermissionDenied()
    return case


@router.get("/cases/{case_id}/evidence", response_model=List[EvidenceOut])
def list_case_evidence(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    category: Optional[str] = None,
    include_archived: bool = True,
):
    case = load_case(db, case_id, user)
    stmt = select(Evidence).where(Evidence.case_id == case.id)
    if category:
        stmt = stmt.where(Evidence.category == category)
    if not include_archived:
        stmt = stmt.where(Evidence.is_archived.is_(False))
    rows = db.execute(stmt.order_by(Evidence.category, Evidence.id)).scalars().all()
    return [serialize_evidence(e) for e in rows]


@router.post("/cases/{case_id}/evidence", response_model=EvidenceOut, status_code=201)
async def upload_evidence(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    file: UploadFile = File(...),
    category: str = Form(EvidenceCategory.EXTERNAL_REFERENCE.value),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    strength: int = Form(60),
    _: None = CsrfProtected,
):
    case = _case_or_403(db, case_id, user)

    if category not in EvidenceCategory.values():
        raise ValidationFailed(f"category must be one of {EvidenceCategory.values()}")

    ext = _safe_extension(file.filename or "")
    accepted_mimes, evidence_type = UPLOAD_KINDS[ext]
    if file.content_type and not any(
        file.content_type.startswith(m.split("/")[0] + "/") and
        (file.content_type in accepted_mimes or file.content_type == m)
        for m in accepted_mimes
    ):
        # Declared type disagrees with the extension - suspicious, refuse early.
        raise UploadRejected(
            f"The file's declared type ({file.content_type}) does not match its "
            f"{ext} extension."
        )

    # Read with a hard cap so a huge upload cannot exhaust memory.
    content = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if len(content) > settings.max_upload_bytes:
        raise UploadRejected(
            f"File exceeds the {settings.max_upload_mb} MB limit."
        )
    if not content:
        raise UploadRejected("The uploaded file is empty.")
    if not _sniff_ok(ext, content):
        raise UploadRejected(
            "The file's contents do not match its extension. Upload rejected."
        )

    # Store under a generated name, partitioned by vendor and case.
    rel_dir = os.path.join(f"vendor_{case.vendor_id}", f"case_{case.id}")
    abs_dir = os.path.join(settings.upload_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    abs_path = os.path.join(abs_dir, stored_name)
    with open(abs_path, "wb") as fh:
        fh.write(content)
    try:
        os.chmod(abs_path, 0o640)
    except OSError:  # pragma: no cover - platform dependent
        pass

    ev = Evidence(
        evidence_ref=next_evidence_ref(db, case.id),
        case_id=case.id,
        vendor_id=case.vendor_id,
        category=category,
        evidence_type=(
            EvidenceType.SCREENSHOT.value
            if category == EvidenceCategory.PRODUCT_IMAGE.value
            and evidence_type == EvidenceType.IMAGE.value
            else evidence_type
        ),
        title=(title or file.filename or "Uploaded evidence")[:512],
        description=description,
        source=f"Uploaded by {user.email}",
        strength=max(0, min(100, strength)),
        checksum=sha256_bytes(content),
        file_path=os.path.join(rel_dir, stored_name),
        file_name=os.path.basename(file.filename or stored_name)[:512],
        mime_type=file.content_type,
        file_size=len(content),
        collected_by=f"USER:{user.id}",
        uploaded_by_id=user.id,
        collected_at=utcnow(),
    )
    db.add(ev)
    db.flush()

    audit.record(
        db, action=AuditAction.EVIDENCE_UPLOADED.value, user=user, request=request,
        object_type="evidence", object_id=ev.id,
        object_label=f"{ev.evidence_ref} on {case.case_number}",
        vendor_id=case.vendor_id,
        new_value={
            "evidence_ref": ev.evidence_ref, "category": ev.category,
            "file_name": ev.file_name, "size": ev.file_size,
            "sha256": ev.checksum,
        },
    )
    db.commit()
    db.refresh(ev)
    return serialize_evidence(ev)


@router.post("/cases/{case_id}/evidence/url", response_model=EvidenceOut, status_code=201)
def add_url_evidence(
    case_id: int,
    payload: EvidenceUrlCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    case = _case_or_403(db, case_id, user)
    if payload.category not in EvidenceCategory.values():
        raise ValidationFailed(f"category must be one of {EvidenceCategory.values()}")

    from app.auth.security import sha256_text

    ev = Evidence(
        evidence_ref=next_evidence_ref(db, case.id),
        case_id=case.id,
        vendor_id=case.vendor_id,
        category=payload.category,
        evidence_type=EvidenceType.URL.value,
        title=payload.title,
        description=payload.description,
        source=f"Added by {user.email}",
        source_url=payload.url,
        strength=payload.strength,
        checksum=sha256_text(payload.url),
        collected_by=f"USER:{user.id}",
        uploaded_by_id=user.id,
        collected_at=utcnow(),
    )
    db.add(ev)
    db.flush()
    audit.record(
        db, action=AuditAction.EVIDENCE_UPLOADED.value, user=user, request=request,
        object_type="evidence", object_id=ev.id,
        object_label=f"{ev.evidence_ref} on {case.case_number}",
        vendor_id=case.vendor_id,
        new_value={"evidence_ref": ev.evidence_ref, "url": payload.url,
                   "category": ev.category},
    )
    db.commit()
    db.refresh(ev)
    return serialize_evidence(ev)


def _load_evidence(db: Session, evidence_id: int, user: User) -> Evidence:
    stmt = scope_to_vendor(
        select(Evidence).where(Evidence.id == evidence_id), Evidence, user
    )
    ev = db.execute(stmt).scalars().first()
    if ev is None:
        raise NotFound("Evidence not found.")
    return ev


@router.get("/evidence", response_model=Page[EvidenceOut])
def list_evidence(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    category: Optional[str] = None,
    relevance: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    from sqlalchemy import func, or_

    stmt = scope_to_vendor(select(Evidence), Evidence, user)
    if category:
        stmt = stmt.where(Evidence.category == category)
    if relevance:
        stmt = stmt.where(Evidence.relevance == relevance)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Evidence.title.ilike(like),
            Evidence.evidence_ref.ilike(like),
            Evidence.description.ilike(like),
        ))
    total = db.execute(
        select(func.count()).select_from(stmt.with_only_columns(Evidence.id).subquery())
    ).scalar_one()
    rows = db.execute(
        stmt.order_by(Evidence.collected_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return Page.build([serialize_evidence(e) for e in rows], total, page, page_size)


@router.put("/evidence/{evidence_id}/review", response_model=EvidenceOut)
def review_evidence(
    evidence_id: int,
    payload: EvidenceReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    if not user.has_permission(Permission.EVIDENCE_REVIEW.value):
        raise PermissionDenied()
    ev = _load_evidence(db, evidence_id, user)

    previous = ev.relevance
    ev.relevance = payload.relevance
    ev.relevance_note = payload.note
    ev.reviewed_by_id = user.id
    ev.reviewed_at = utcnow()
    db.add(ev)

    audit.record(
        db, action=AuditAction.EVIDENCE_REVIEWED.value, user=user, request=request,
        object_type="evidence", object_id=ev.id, object_label=ev.evidence_ref,
        vendor_id=ev.vendor_id,
        previous_value={"relevance": previous},
        new_value={"relevance": ev.relevance, "note": payload.note},
    )
    db.commit()
    db.refresh(ev)
    return serialize_evidence(ev)


@router.post("/evidence/{evidence_id}/archive", response_model=EvidenceOut)
def archive_evidence(
    evidence_id: int,
    request: Request,
    reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    """Evidence is never deleted. Archiving requires a reason and stays visible."""
    if not user.has_permission(Permission.EVIDENCE_REVIEW.value):
        raise PermissionDenied()
    if not (reason or "").strip():
        raise ValidationFailed("A reason is required to archive evidence.")

    ev = _load_evidence(db, evidence_id, user)
    ev.is_archived = True
    ev.archived_reason = reason.strip()[:512]
    ev.archived_by_id = user.id
    ev.archived_at = utcnow()
    db.add(ev)
    audit.record(
        db, action=AuditAction.EVIDENCE_REVIEWED.value, user=user, request=request,
        object_type="evidence", object_id=ev.id, object_label=ev.evidence_ref,
        vendor_id=ev.vendor_id,
        previous_value={"is_archived": False},
        new_value={"is_archived": True, "reason": ev.archived_reason},
        detail="Evidence archived (not deleted).",
    )
    db.commit()
    db.refresh(ev)
    return serialize_evidence(ev)


@router.get("/evidence/{evidence_id}/download")
def download_evidence(
    evidence_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ev = _load_evidence(db, evidence_id, user)
    if not ev.file_path:
        raise NotFound("This evidence item has no stored file.")

    root = os.path.realpath(settings.upload_dir)
    path = os.path.realpath(os.path.join(root, ev.file_path))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        raise NotFound("The stored file is no longer available.")

    audit.record(
        db, action=AuditAction.CASE_VIEWED.value, user=user, request=request,
        object_type="evidence", object_id=ev.id, object_label=ev.evidence_ref,
        vendor_id=ev.vendor_id, detail="Evidence file downloaded", commit=True,
    )
    return FileResponse(
        path,
        media_type=ev.mime_type or "application/octet-stream",
        filename=ev.file_name or f"{ev.evidence_ref}",
        headers={"X-Content-Type-Options": "nosniff",
                 "Content-Disposition": f'attachment; filename="{ev.file_name}"'},
    )
