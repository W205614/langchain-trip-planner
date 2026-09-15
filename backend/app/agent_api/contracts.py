"""Version-one non-streaming wire requests; shared with Java as JSON Schema."""
from typing import Literal, Any
from pydantic import BaseModel, Field, model_validator


class DocumentExtractRequest(BaseModel):
    document_id: int = Field(ge=1)
    document_version: int = Field(ge=1)
    city: str = Field(min_length=1,max_length=64)
    title: str = Field(min_length=1,max_length=160)


class DocumentExtractResult(BaseModel):
    pages: list[str] = Field(min_length=1,max_length=10)


class DocumentIndexRequest(BaseModel):
    operation: Literal["upsert","delete"]
    document_id: int = Field(ge=1)
    document_version: int = Field(ge=1)
    document: dict[str,Any] | None = None

    @model_validator(mode="after")
    def current_record(self):
        if self.operation=="upsert" and (not self.document or self.document.get("id")!=self.document_id or self.document.get("version")!=self.document_version):
            raise ValueError("Upsert requires matching document identity and version")
        return self


class HistoryIndexRequest(BaseModel):
    operation: Literal["upsert","delete"]
    record_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    job_id: int = Field(ge=1)
    record: dict[str,Any] | None = None

    @model_validator(mode="after")
    def owned_record(self):
        if self.operation=="upsert" and (not self.record or self.record.get("id")!=self.record_id or self.record.get("user_id")!=self.user_id):
            raise ValueError("Upsert requires matching record and owner")
        return self


class EvidenceVisibilityRequest(BaseModel):
    user_id: int | None = Field(default=None,ge=1)
    candidates: list[dict[str,Any]] = Field(max_length=100)


class EvidenceVisibilityResult(BaseModel):
    allowed: list[int]


class EvidenceSnapshot(BaseModel):
    revision: int = Field(ge=0)
    documents: list[dict[str,Any]]
    records: list[dict[str,Any]]


class IndexRebuildResult(BaseModel):
    success: bool
    message: str
    chunks: int = Field(ge=0)
    history_records: int | None = Field(default=None,ge=0)
    generation: str | None = None


WIRE_MODELS=(DocumentExtractRequest,DocumentExtractResult,DocumentIndexRequest,HistoryIndexRequest,
    EvidenceVisibilityRequest,EvidenceVisibilityResult,EvidenceSnapshot,IndexRebuildResult)
