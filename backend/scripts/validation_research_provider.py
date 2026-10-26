"""Offline index provider for the isolated browser stack, never production imports.

Publication, ownership and version visibility remain real Java HTTP decisions.
This replaces only embedding/vector storage and provider generation.
"""
import os
import re
from threading import RLock
from types import SimpleNamespace
from app.agent_api.business_client import visible

if os.environ.get("APP_ENV")!="validation" or os.environ.get("VALIDATION_ALLOW_FIXTURES")!="yes":
    raise RuntimeError("Research fixture is restricted to the isolated validation stack")


class FixtureIndex:
    enabled=True
    _embedding=SimpleNamespace(model="offline-index-provider-no-embedding")

    def __init__(self):
        self._documents={}
        self._lock=RLock()
        self.retrieval_calls=0
        self.embedding_calls=0

    def is_ready(self):return True
    def add_history_plan(self,*args,**kwargs):return True
    def delete_history_plan(self,*args,**kwargs):return True
    def build_rag_context(self,*args,**kwargs):return ""
    def get_knowledge_attractions(self,*args,**kwargs):return []
    def get_attraction_rag_texts(self,*args,**kwargs):return {}

    def replace_public_knowledge_document(self,identity,city,title,pages,source_tier="community",document_version=1):
        docs=[SimpleNamespace(page_content=page,metadata={"city":city,"source":title,"page":i,"source_type":"multimodal",
            "source_tier":source_tier,"document_id":int(identity),"document_version":int(document_version),"chunk_id":f"document:{identity}:{i}"}) for i,page in enumerate(pages,1)]
        with self._lock:self._documents[int(identity)]=docs
        return True

    def delete_public_knowledge_document(self,identity):
        with self._lock:self._documents.pop(int(identity),None)
        return True

    def retrieve_research_evidence(self,query,city,k=5,local_only=False):
        # A deterministic lexical provider; neither path invokes embeddings or
        # auto-creates city knowledge. Version/publication checks still cross
        # the actual internal Java endpoint on every nonempty result.
        needle=query.strip().casefold()
        with self._lock:
            self.retrieval_calls+=1
            documents=[document for docs in self._documents.values() for document in docs
                if document.metadata["city"]==city and needle and needle in (document.page_content+" "+document.metadata["source"]).casefold()][:max(1,min(k,8))]
        return [{"content":doc.page_content,**doc.metadata} for doc in visible(documents)]


def answer_from_public_excerpt(messages):
    """A deterministic provider response; real support validation runs afterward."""
    text=messages[-1].content
    block=text.partition("公开资料：")[2]
    match=re.match(r"\s*\[1\] 来源：([^\n]+)\n(.*?)(?=\n\n\[\d+\] 来源：|\Z)",block,re.S)
    if not match:raise ValueError("Offline answer provider requires a numbered public source")
    title,excerpt=match.group(1).strip(),match.group(2).strip()
    # Production _messages collapses whitespace inside each excerpt, so a
    # Markdown title and its first sentence can arrive on the same line.
    # Prefer an exact repetition of the declared source title. A reviewed
    # document can also contain a different, single-token section heading.
    # Removing that token produces only a candidate: the production support
    # check still rejects a remaining multiword heading joined to the body.
    repeated_title=re.match(r"^#{1,6}\s+"+re.escape(title)+r"(?:\s+|$)",excerpt)
    if repeated_title:excerpt=excerpt[repeated_title.end():].strip()
    else:excerpt=re.sub(r"^#{1,6}\s+\S+\s+","",excerpt,count=1)
    lines=[re.sub(r"^[-*+]\s+","",line.strip()) for line in excerpt.splitlines()]
    candidates=[line for line in lines if line and not line.startswith(("[","#","来源页"))]
    if not candidates:raise ValueError("Offline answer provider requires a public source excerpt")
    # Select a complete original sentence, never paraphrase or drop a clause.
    sentence=re.match(r".*?[。！？]",candidates[0])
    return (sentence.group(0) if sentence else candidates[0])+"[1]"
