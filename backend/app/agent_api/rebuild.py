"""Rebuild a new generation from a Java-owned repeatable-read snapshot."""
import json
import os
import hashlib
import math
from uuid import uuid4
from langchain_core.documents import Document
from langchain_chroma import Chroma
from ..models.schemas import TripRequest, TripPlan
from ..services.rag_service import get_rag_service, CHROMA_DIR, _KNOWLEDGE_COLLECTION, _HISTORY_COLLECTION, _EMBED_BATCH_SIZE
from ..services.knowledge_chunks import sections
from ..services.rag_runtime import index_lock
from .business_client import post
from .indexing import _lock
from ..services.chroma_boundary import embedded_settings


def _save_state(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def rebuild(rag=None, *, preview=False, restart=False):
    with _lock:
        rag=rag if rag is not None else get_rag_service()
        if rag._embedding is None:
            return {"success":False,"message":"嵌入服务未配置","chunks":0}
        snapshot=post("/evidence/snapshot",{})
        mutation=getattr(rag,"_mutation_version",0)
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
        source = {"revision":snapshot["revision"], "identity":rag._embedding.identity,
            "documents":[[d.page_content,d.metadata] for d in documents],
            "history":[[d.page_content,d.metadata] for d in history]}
        fingerprint=hashlib.sha256(json.dumps(source,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        estimate={"success":True,"preview":True,"chunks":len(documents),"history_records":len(history),
            "embedding_identity":rag._embedding.identity,"source_fingerprint":fingerprint,
            "estimated_embedding_requests":sum(math.ceil(min(16,len(items)-offset)/_EMBED_BATCH_SIZE)
                for items in (documents,history) for offset in range(0,len(items),16))+bool(documents)+bool(history),
            "input_utf8_bytes":sum(len(d.page_content.encode()) for d in documents+history)}
        if preview:return estimate
        state_path=CHROMA_DIR/"rebuild-state.json"
        state=json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        if state and state.get("status")=="building" and not restart:
            if state["source_fingerprint"]!=fingerprint:
                raise RuntimeError("Rebuild source changed; preview and explicitly restart a new generation")
        else:
            generation=uuid4().hex
            state={"generation":generation,"source_fingerprint":fingerprint,"status":"building",
                "offsets":{_KNOWLEDGE_COLLECTION:0,_HISTORY_COLLECTION:0},
                "names":{**{key:f"{key}_{generation}" for key in (_KNOWLEDGE_COLLECTION,_HISTORY_COLLECTION)},
                    "embedding_model":rag._embedding.model,"embedding_identity":rag._embedding.identity}}
            CHROMA_DIR.mkdir(parents=True,exist_ok=True)
            _save_state(state_path,state)
        generation=state["generation"];names=state["names"]
        stores={key:Chroma(collection_name=names[key],embedding_function=rag._embedding,persist_directory=str(CHROMA_DIR),client_settings=embedded_settings(CHROMA_DIR))
                for key in (_KNOWLEDGE_COLLECTION,_HISTORY_COLLECTION)}
        for key,items,identities in ((_KNOWLEDGE_COLLECTION,documents,[f"document-{i}" for i in range(len(documents))]),(_HISTORY_COLLECTION,history,ids)):
            for offset in range(state["offsets"][key],len(items),16):
                stores[key].add_documents(items[offset:offset+16],ids=identities[offset:offset+16])
                state["offsets"][key]=min(offset+16,len(items))
                _save_state(state_path,state)
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
            state["status"]="complete"
            _save_state(state_path,state)
        return {"success":True,"message":"新索引已验证并切换，旧集合保留","chunks":len(documents),"history_records":len(history),"generation":generation}
