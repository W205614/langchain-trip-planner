"""公共图文旅游知识的投稿、审核与删除接口。"""

from pathlib import Path
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy.orm import Session

from ...core.security import get_current_user, require_admin
from ...db.database import DATA_DIR, get_db
from ...db.models import KnowledgeDocument, KnowledgeIngestJob, User
from ...services.knowledge_ingest import MAX_UPLOAD_BYTES, content_hash, detect_upload_type, save_uploaded_content
from ...services.coordination import knowledge_serialized
from fastapi.responses import FileResponse

router = APIRouter(prefix="/knowledge", tags=["公共知识库"])


def _serialize(document: KnowledgeDocument) -> dict:
    return {
        "id": document.id, "city": document.city, "title": document.title,
        "original_filename": document.original_filename, "status": document.status,
        "source_tier": document.source_tier,
        "review_note": document.review_note, "page_count": document.page_count,
        "version": document.version,
        "submitted_by": document.submitted_by, "reviewed_by": document.reviewed_by,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


class ReviewRequest(BaseModel):
    note: str = Field(default="", max_length=500)
    source_tier: Literal["community", "reviewed", "official"] = "community"


class ExtractedReview(BaseModel):
    version: int = Field(ge=1)
    pages: list[str] = Field(min_length=1, max_length=10)


class PublishReview(BaseModel):
    version: int = Field(ge=1)


@router.get("/admin/submissions/{document_id}/preview")
def preview(document_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    document = db.get(KnowledgeDocument, document_id)
    if document is None or document.status == "deleted":
        raise HTTPException(404, "资料不存在")
    return {"data": {**_serialize(document), "pages": json.loads(document.extracted_pages_json),
                     "legacy_review": document.status == "published" and document.extracted_pages_json == "[]"}}


@router.get("/admin/submissions/{document_id}/original")
def original(document_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    from ...services.knowledge_ingest import upload_path
    document = db.get(KnowledgeDocument, document_id)
    if document is None or document.status == "deleted" or not upload_path(document.stored_path).is_file():
        raise HTTPException(404, "原文件不存在")
    return FileResponse(upload_path(document.stored_path), media_type=document.media_type,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.put("/admin/submissions/{document_id}/extraction")
@knowledge_serialized
def edit_extraction(document_id: int, body: ExtractedReview, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(404, "资料不存在")
    if document.version != body.version or document.status != "awaiting_review":
        raise HTTPException(409, "资料已变更，请重新打开复核")
    if len(body.pages) != document.page_count or any(not p.strip() or len(p) > 12000 for p in body.pages):
        raise HTTPException(422, "请保留原页数，每页须有内容且不超过12000字")
    document.extracted_pages_json = json.dumps(body.pages, ensure_ascii=False)
    document.version += 1
    db.commit()
    return {"data": _serialize(document)}


@router.post("/admin/submissions/{document_id}/publish")
@knowledge_serialized
def publish(document_id: int, body: PublishReview, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(404, "资料不存在")
    if document.version != body.version or document.status != "awaiting_review":
        raise HTTPException(409, "只能发布当前版本已复核的提取结果")
    pages = json.loads(document.extracted_pages_json)
    if not pages or any(not p.strip() for p in pages):
        raise HTTPException(422, "提取内容为空，不能发布")
    document.status, document.reviewed_by = "publishing", user.id
    db.add(KnowledgeIngestJob(document_id=document.id, document_version=document.version, status="pending"))
    db.commit()
    return {"success": True, "data": _serialize(document), "message": "已确认当前版本，等待发布"}


@router.post("/submissions", status_code=status.HTTP_201_CREATED, summary="提交公共旅游图文资料")
async def submit_knowledge(
    city: str = Form(..., min_length=1, max_length=64),
    title: str = Form(..., min_length=1, max_length=160),
    file: UploadFile = File(...),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件不能超过 20 MB")
    try:
        media_type, suffix = detect_upload_type(content)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    document = KnowledgeDocument(
        submitted_by=current_user.id, city=city.strip(), title=title.strip(),
        original_filename=Path(file.filename or f"upload{suffix}").name,
        stored_path="", sha256=content_hash(content), media_type=media_type, status="pending",
    )
    db.add(document)
    db.flush()
    try:
        document.stored_path = save_uploaded_content(document.id, content, suffix)
        db.commit()
        db.refresh(document)
    except Exception:
        stored_path, document_id = document.stored_path, document.id
        db.rollback()
        # A commit may have succeeded even if the acknowledgement/refresh failed.
        # Only clean up after an authoritative absence check; retain uncertain files.
        absent = False
        try:
            absent = db.get(KnowledgeDocument, document_id) is None
        except Exception:
            db.rollback()
        if stored_path and absent:
            from ...services.knowledge_ingest import upload_path
            try:
                upload_path(stored_path).unlink(missing_ok=True)
            except OSError:
                import logging
                logging.getLogger(__name__).warning("Uncommitted upload cleanup failed; inspect orphan files")
        raise
    return {"success": True, "message": "资料已提交，管理员审核后会公开到知识库", "data": _serialize(document)}


@router.get("/submissions/mine", summary="查看我的资料投稿")
def my_submissions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    documents = db.query(KnowledgeDocument).filter(KnowledgeDocument.submitted_by == current_user.id).order_by(KnowledgeDocument.id.desc()).all()
    return {"success": True, "data": [_serialize(item) for item in documents]}


@router.get("/admin/submissions", summary="管理员查看待审核资料")
def admin_submissions(status_filter: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    query = db.query(KnowledgeDocument)
    if status_filter:
        query = query.filter(KnowledgeDocument.status == status_filter)
    return {"success": True, "data": [_serialize(item) for item in query.order_by(KnowledgeDocument.id.desc()).all()]}


@router.post("/admin/submissions/{document_id}/approve", summary="审核通过并进入解析队列")
@knowledge_serialized
def approve_submission(document_id: int, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if document.status not in {"pending", "rejected", "failed"}:
        raise HTTPException(status_code=409, detail="当前状态不能重复审核")
    document.version += 1
    document.status, document.reviewed_by, document.review_note = "queued", current_user.id, body.note.strip()
    document.source_tier = body.source_tier
    db.add(KnowledgeIngestJob(document_id=document.id, document_version=document.version, status="pending"))
    db.commit()
    return {"success": True, "message": "正在解析；提取完成后须再次复核确认发布", "data": _serialize(document)}


@router.post("/admin/submissions/{document_id}/reject", summary="拒绝资料投稿")
@knowledge_serialized
def reject_submission(document_id: int, body: ReviewRequest, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if document.status in {"published", "deleted"}:
        raise HTTPException(status_code=409, detail="已发布资料请使用删除接口")
    document.status, document.reviewed_by = "rejected", current_user.id
    document.version += 1
    document.review_note = body.note.strip() or "管理员拒绝发布"
    db.commit()
    return {"success": True, "message": "已拒绝该资料", "data": _serialize(document)}


@router.delete("/admin/submissions/{document_id}", summary="删除已发布或待审资料")
@knowledge_serialized
def delete_submission(document_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    if document.status == "deleted":
        return {"success": True, "message": "资料已删除"}
    document.status = "deleted"
    document.version += 1
    document.source_text = ""
    db.add(KnowledgeIngestJob(document_id=document.id, document_version=document.version, status="pending"))
    db.commit()
    return {"success": True, "message": "资料已不可检索，文件与向量清理已排队"}
