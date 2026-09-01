from unittest.mock import MagicMock
from uuid import uuid4

from app.db.database import SessionLocal
from app.db.models import KnowledgeDocument, User
from app.services import knowledge_ingest as ingest


JPEG = b"\xff\xd8\xff\xe0" + b"test-image"


def _token(client, username: str) -> str:
    response = client.post("/api/auth/register", json={"username": username, "password": "secret12"})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_image_submission_requires_admin_review(client, monkeypatch):
    username = f"knowledge_{uuid4().hex[:8]}"
    token = _token(client, username)
    response = client.post(
        "/api/knowledge/submissions",
        headers={"Authorization": f"Bearer {token}"},
        data={"city": "北京", "title": "故宫攻略"},
        files={"file": ("guide.jpg", JPEG, "image/jpeg")},
    )
    assert response.status_code == 201
    document = response.json()["data"]
    assert document["status"] == "pending"

    denied = client.get("/api/knowledge/admin/submissions", headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 403

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        user.is_admin = True
        db.commit()
    finally:
        db.close()
    approved = client.post(
        f"/api/knowledge/admin/submissions/{document['id']}/approve",
        headers={"Authorization": f"Bearer {token}"}, json={"note": "来源可信", "source_tier": "official"},
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "queued"
    assert approved.json()["data"]["source_tier"] == "official"

    db = SessionLocal()
    try:
        document_row = db.get(KnowledgeDocument, document["id"])
        document_row.status = "published"
        db.commit()
    finally:
        db.close()
    rag = MagicMock(enabled=True)
    rag.delete_public_knowledge_document.return_value = True
    monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: rag)
    removed = client.delete(f"/api/knowledge/admin/submissions/{document['id']}", headers={"Authorization": f"Bearer {token}"})
    assert removed.status_code == 200
    rag.delete_public_knowledge_document.assert_called_once_with(document["id"])


def test_process_document_publishes_page_text_with_source(monkeypatch, tmp_path):
    db = SessionLocal()
    try:
        monkeypatch.setattr(ingest, "DATA_DIR", tmp_path)
        monkeypatch.setattr(ingest, "UPLOAD_DIR", tmp_path / "knowledge_uploads")
        path = tmp_path / "knowledge_uploads" / "1.jpg"
        path.parent.mkdir()
        path.write_bytes(JPEG)
        document = KnowledgeDocument(
            submitted_by=1, city="北京", title="故宫攻略", original_filename="guide.jpg",
            stored_path="knowledge_uploads/1.jpg", sha256="a" * 64, media_type="image/jpeg", status="queued",
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        extractor = MagicMock()
        extractor.extract.return_value = ingest.VisionExtraction(summary="故宫参观建议", facts=["建议提前预约"])
        rag = MagicMock(enabled=True)
        rag.replace_public_knowledge_document.return_value = True
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: rag)

        ingest.process_document(db, document, extractor=extractor)

        assert document.status == "published"
        assert document.page_count == 1
        assert "来源页: 1" in document.source_text
        rag.replace_public_knowledge_document.assert_called_once()
        assert rag.replace_public_knowledge_document.call_args.args[:3] == (document.id, "北京", "故宫攻略")
        assert rag.replace_public_knowledge_document.call_args.kwargs["source_tier"] == "community"
    finally:
        db.rollback()
        db.close()


def test_scanned_pdf_is_rendered_before_vision_extraction(monkeypatch, tmp_path):
    import pymupdf

    db = SessionLocal()
    try:
        monkeypatch.setattr(ingest, "DATA_DIR", tmp_path)
        path = tmp_path / "knowledge_uploads" / "2.pdf"
        path.parent.mkdir()
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Beijing guide")
        pdf.new_page().insert_text((72, 72), "Forbidden City")
        pdf.save(path)
        pdf.close()
        document = KnowledgeDocument(
            submitted_by=1, city="北京", title="两页攻略", original_filename="guide.pdf",
            stored_path="knowledge_uploads/2.pdf", sha256="b" * 64, media_type="application/pdf", status="queued",
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        extractor = MagicMock()
        extractor.extract.return_value = ingest.VisionExtraction(facts=["故宫需要预约"])
        rag = MagicMock(enabled=True)
        rag.replace_public_knowledge_document.return_value = True
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: rag)

        ingest.process_document(db, document, extractor=extractor)

        assert extractor.extract.call_count == 2
        assert document.page_count == 2
        assert rag.replace_public_knowledge_document.call_args.args[3][1].startswith("## 两页攻略")
    finally:
        db.rollback()
        db.close()


def test_upload_signature_rejects_spoofed_file():
    try:
        ingest.detect_upload_type(b"not an image")
    except ValueError as exc:
        assert "仅支持" in str(exc)
    else:
        raise AssertionError("unknown content must be rejected")
