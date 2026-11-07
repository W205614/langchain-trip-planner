import chromadb
from app.agent_api import indexing,index_versions
from app.services.chroma_boundary import embedded_settings


def test_erasure_purges_retained_generations_without_embedding_service(tmp_path,monkeypatch):
    monkeypatch.setattr(indexing,"CHROMA_DIR",tmp_path/"chroma")
    monkeypatch.setattr(index_versions,"ledger_path",lambda:tmp_path/"commands.sqlite3")
    monkeypatch.setattr(indexing,"get_rag_service",lambda:(_ for _ in ()).throw(AssertionError("Deletion must not initialize models")))
    client=chromadb.PersistentClient(path=str(tmp_path/"chroma"),settings=embedded_settings(tmp_path/"chroma"))
    for name in ["trip_history","trip_history_retired","trip_history_building"]:
        store=client.create_collection(name)
        store.add(ids=["mine","other"],embeddings=[[1.,0.,0.],[0.,1.,0.]],documents=["private","other"],
            metadatas=[{"record_id":42,"user_id":7},{"record_id":42,"user_id":8}])
    assert indexing.history({"operation":"delete","record_id":42,"user_id":7,"job_id":9})["success"]
    for collection in client.list_collections():assert collection.get()["ids"]==["other"]
    assert not index_versions.begin("history:7:42",8)


def test_document_erasure_deletes_all_copies_and_keeps_other_documents(tmp_path,monkeypatch):
    monkeypatch.setattr(indexing,"CHROMA_DIR",tmp_path/"chroma")
    monkeypatch.setattr(index_versions,"ledger_path",lambda:tmp_path/"commands.sqlite3")
    client=chromadb.PersistentClient(path=str(tmp_path/"chroma"),settings=embedded_settings(tmp_path/"chroma"))
    for name in ["trip_knowledge","trip_knowledge_retired"]:
        store=client.create_collection(name)
        store.add(ids=["mine","other"],embeddings=[[1.,0.,0.],[0.,1.,0.]],documents=["private","other"],
            metadatas=[{"document_id":3},{"document_id":4}])
    assert indexing.document({"operation":"delete","document_id":3,"document_version":5})["success"]
    for collection in client.list_collections():assert collection.get()["ids"]==["other"]
