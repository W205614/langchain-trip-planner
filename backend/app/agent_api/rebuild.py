"""Rebuild a new generation from a Java-owned repeatable-read snapshot."""
import json
import os
from uuid import uuid4
from langchain_core.documents import Document
from langchain_chroma import Chroma
from ..models.schemas import TripRequest, TripPlan
from ..services.rag_service import get_rag_service, CHROMA_DIR, _KNOWLEDGE_COLLECTION, _HISTORY_COLLECTION
from ..services.knowledge_chunks import sections
from ..services.rag_runtime import index_lock
from .business_client import post
from .indexing import _lock


def rebuild(rag=None):
    with _lock:
        rag=rag if rag is not None else get_rag_service()
        if rag._embedding is None:
            return {"success":False,"message":"嵌入服务未配置","chunks":0}
        snapshot=post("/evidence/snapshot",{})
        mutation=getattr(rag,"_mutation_version",0)
        generation=uuid4().hex
        names={key:f"{key}_{generation}" for key in (_KNOWLEDGE_COLLECTION,_HISTORY_COLLECTION)}
        names["embedding_model"]=rag._embedding.model
        stores={key:Chroma(collection_name=names[key],embedding_function=rag._embedding,persist_directory=str(CHROMA_DIR))
                for key in (_KNOWLEDGE_COLLECTION,_HISTORY_COLLECTION)}
        documents=rag._load_knowledge_documents()
        if rag._knowledge_store is not None:
            old=rag._knowledge_store.get(include=["documents","metadatas"])
            documents.extend(Document(page_content=text,metadata=meta) for text,meta in zip(old.get("documents",[]),old.get("metadatas",[]))
                             if (meta or {}).get("source","").startswith("gaode:"))
        for row in snapshot["documents"]:
            pages=json.loads(row["extracted_pages_json"]) or row["source_text"].split("\n\n## ")
            if not row["source_text"].strip() and not any(pages):
                raise ValueError("Published source text missing")
            for page,text in enumerate(pages,1):
                for index,section in enumerate(sections(text)):
                    documents.append(Document(page_content=section["text"],metadata={"city":row["city"],"source":row["title"],"source_type":"multimodal",
                        "source_tier":row["source_tier"],"document_id":row["id"],"document_version":row["version"],"page":page,
                        "chunk_id":f"submission:{row['id']}:{page}:{index}","heading_path":section["heading_path"],"entity_name":section["entity_name"]}))
        history=[];ids=[]
        for row in snapshot["records"]:
            plan=TripPlan.model_validate_json(row["plan_json"])
            request=TripRequest(**{k:row[k] for k in ("city","start_date","end_date","travel_days","transportation","accommodation")},
                departure_city=row.get("departure_city", ""), traveler_count=row.get("traveler_count", 1),
                room_count=row.get("room_count", 1), budget_total=row.get("budget_total"),
                preferences=json.loads(row["preferences"]),constraints=plan.constraints)
            history.append(Document(page_content=rag._plan_to_text(request,plan),metadata={"record_id":row["id"],"user_id":row["user_id"],"record_version":row["version"],"city":row["city"]}))
            ids.append(f"history-{row['user_id']}-{row['id']}")
        if documents:stores[_KNOWLEDGE_COLLECTION].add_documents(documents)
        if history:stores[_HISTORY_COLLECTION].add_documents(history,ids=ids)
        for key,expected in ((_KNOWLEDGE_COLLECTION,len(documents)),(_HISTORY_COLLECTION,len(history))):
            if stores[key]._collection.count()!=expected:raise ValueError("New index count mismatch")
            if expected:stores[key].similarity_search("验证索引",k=1)
        if post("/evidence/revision",{})["revision"]!=snapshot["revision"]:
            raise RuntimeError("Business evidence changed during rebuild; old index retained")
        with index_lock():
            if getattr(rag,"_mutation_version",0)!=mutation:
                raise RuntimeError("Agent index changed during rebuild; old index retained")
            temporary=CHROMA_DIR/f"active-{generation}.tmp"
            with temporary.open("w",encoding="utf-8") as file:
                json.dump(names,file);file.flush();os.fsync(file.fileno())
            os.replace(temporary,CHROMA_DIR/"active-index.json")
            rag._collection_names=names;rag._knowledge_store=stores[_KNOWLEDGE_COLLECTION];rag._history_store=stores[_HISTORY_COLLECTION];rag._degraded=False
        return {"success":True,"message":"新索引已验证并切换，旧集合保留","chunks":len(documents),"history_records":len(history),"generation":generation}
