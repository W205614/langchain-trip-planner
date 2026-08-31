"""RAG 知识库服务: 城市旅游知识文档 + 历史行程 的向量化存储与检索

数据源:
1. backend/data/knowledge/*.md — 预设城市旅游知识库 (启动时自动索引, 可手动重建)
2. 历史行程记录 (TripRecord)   — 每次生成行程后增量入库

嵌入模型: OpenAI 兼容接口 (默认复用 LLM 的中转 base_url/api_key)
         当前: text-embedding-3-large (3072 维, 走 api.openai-proxy.org 中转)
向量库:   ChromaDB (持久化到 backend/data/chroma)

降级策略: 未配置可用的嵌入 key/base_url 或初始化失败时, RAG 整体禁用,
          所有检索返回空, 不影响旅行规划主流程。
注意:     Chroma 集合的向量维度与嵌入模型绑定。切换嵌入模型后,
          旧集合(如 bge-m3 的 1024 维)需重建, 否则入库会报维度冲突
          (Collection expecting embedding with dimension of X, got Y)。
"""

import logging
import os
import re
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings as LangChainEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config import get_settings
from ..core.rag_metrics import observe_rag_operation
from ..models.schemas import TripPlan, TripRequest

logger = logging.getLogger(__name__)

# 嵌入单次调用批量上限 (text-embedding-3-large 一次最多 2048 条, 这里保守分批)
_EMBED_BATCH_SIZE = 16

# 知识库文件名(英文) → 城市中文名 (与前端请求的城市保持一致, 用于检索过滤)
_CITY_NAME_MAP = {
    "shenzhen": "深圳",
    "beijing": "北京",
    "shanghai": "上海",
    "guangzhou": "广州",
}

# 高德动态入库的知识文档 source 标记前缀。source = f"{_GAODE_SOURCE_PREFIX}{城市}"
# 用于幂等判断: 同一城市的高德自动数据只写入一次, 避免每次查询重复入库膨胀向量库。
_GAODE_SOURCE_PREFIX = "gaode:"

# 高德动态入库: 单城市最多写入的景点知识块数(控制向量库膨胀与检索噪音)
_GAODE_CITY_MAX_CHUNKS = 12


class _OpenAICompatEmbeddings(LangChainEmbeddings):
    """嵌入模型 (OpenAI 兼容接口)

    通过中转/兼容端点 (如 SiliconFlow 等, 复用 LLM 的 base_url + api_key)
    调用 text-embedding 模型, 走 langchain_openai.OpenAIEmbeddings;
    实现 LangChain Embeddings 接口 (embed_documents/embed_query), 供 ChromaDB 使用。
    """

    def __init__(self, api_key: str, base_url: str, model: str = "text-embedding-3-large"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _client(self):
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            check_embedding_ctx_length=False,
        )

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        # 部分中转接口单次限制 10 条, 超出分批 (OpenAI 官方单次上限 2048 条)
        cleaned = [t.replace("\n", " ") for t in texts]
        results: List[List[float]] = []
        for i in range(0, len(cleaned), _EMBED_BATCH_SIZE):
            results.extend(self._client().embed_documents(cleaned[i:i + _EMBED_BATCH_SIZE]))
        return results

    def embed_query(self, text: str) -> List[float]:
        return self._client().embed_query(text.replace("\n", " "))

# backend/data/knowledge 与 backend/data/chroma
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
CHROMA_DIR = DATA_DIR / "chroma"

_KNOWLEDGE_COLLECTION = "trip_knowledge"
_HISTORY_COLLECTION = "trip_history"


class RagService:
    """RAG 检索服务 (单例)"""

    def __init__(self):
        self.settings = get_settings()
        self._embedding = None
        self._knowledge_store = None
        self._history_store = None
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=300,
            chunk_overlap=50,
            separators=["\n## ", "\n### ", "\n- ", "\n", "。", "；", " "],
        )
        self._init()

    # ============ 初始化 ============

    def _init(self) -> None:
        """初始化嵌入模型与向量库 (失败则降级禁用)"""
        api_key = self.settings.embedding_api_key or self.settings.llm_api_key
        base_url = self.settings.embedding_base_url or self.settings.llm_base_url
        if not api_key or not base_url:
            logger.warning(
                "⚠️  RAG 嵌入未配置 (缺少 EMBEDDING_API_KEY/LLM_API_KEY 或 EMBEDDING_BASE_URL/LLM_BASE_URL), "
                "RAG 知识库功能已禁用 (不影响旅行规划主流程)"
            )
            return
        try:
            from langchain_chroma import Chroma

            os.makedirs(CHROMA_DIR, exist_ok=True)
            self._embedding = _OpenAICompatEmbeddings(
                api_key=api_key,
                base_url=base_url,
                model=self.settings.embedding_model,
            )
            self._ensure_collections_consistent()
            self._knowledge_store = Chroma(
                collection_name=_KNOWLEDGE_COLLECTION,
                embedding_function=self._embedding,
                persist_directory=str(CHROMA_DIR),
            )
            self._history_store = Chroma(
                collection_name=_HISTORY_COLLECTION,
                embedding_function=self._embedding,
                persist_directory=str(CHROMA_DIR),
            )
            logger.info(
                f"✅ RAG 知识库初始化成功 (嵌入: {self.settings.embedding_model} @ {base_url} | "
                f"向量库: ChromaDB@{CHROMA_DIR})"
            )
        except Exception as e:
            logger.warning(f"⚠️  RAG 初始化失败, 已降级禁用: {e}")
            self._embedding = None
            self._knowledge_store = None
            self._history_store = None

    def _ensure_collections_consistent(self) -> None:
        """校验并修复持久化集合的向量维度与当前嵌入模型一致。

        切换嵌入模型(如 bge-m3 1024 维 → text-embedding-3-large 3072 维)后,
        旧的 Chroma 集合仍按旧维度建表, 新向量写入会报
        "Collection expecting embedding with dimension of X, got Y"。
        此处每次启动探测集合维度, 不一致则清空该集合让首次索引/入库按新维度重建。
        """
        try:
            from langchain_chroma import Chroma

            for name in (_KNOWLEDGE_COLLECTION, _HISTORY_COLLECTION):
                store = Chroma(
                    collection_name=name,
                    embedding_function=self._embedding,
                    persist_directory=str(CHROMA_DIR),
                )
                try:
                    col = store._collection
                    count = col.count()
                    if count == 0:
                        # 空集合没有向量, 无需维度校验, 首次写入时会按当前嵌入自动建表
                        continue
                    probe = col.peek(limit=1)
                    stored_dim = len(probe["embeddings"][0])
                    sample = self._embedding.embed_query("维度探测")
                    if stored_dim != len(sample):
                        logger.warning(
                            f"⚠️  集合 [{name}] 向量维度 {stored_dim} 与当前嵌入 {len(sample)} 不一致, "
                            f"清空重建 (可能由切换嵌入模型引起)"
                        )
                        store.delete_collection()
                except Exception as e:
                    logger.warning(f"⚠️  集合 [{name}] 维度探测失败, 按需重建: {e}")
                    store.delete_collection()
        except Exception as e:
            logger.warning(f"⚠️  集合一致性校验失败: {e}")

    @property
    def enabled(self) -> bool:
        """RAG 是否可用"""
        return self._embedding is not None

    def _new_store(self, collection_name: str):
        """(重建用) 创建新的 Chroma 实例"""
        from langchain_chroma import Chroma

        return Chroma(
            collection_name=collection_name,
            embedding_function=self._embedding,
            persist_directory=str(CHROMA_DIR),
        )

    def _refresh_store(self, attribute: str, collection_name: str) -> None:
        """删除/重建集合后，丢弃可能持有旧 collection ID 的 Chroma 实例。"""
        store = getattr(self, attribute)
        try:
            store._collection.count()
        except Exception as exc:
            logger.warning("RAG 集合句柄失效，重新连接: %s", collection_name)
            setattr(self, attribute, self._new_store(collection_name))

    def is_ready(self) -> bool:
        """验证本地 Chroma 句柄可用；RAG 未配置时不阻塞核心服务就绪。"""
        if not self.enabled:
            return True
        self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
        self._refresh_store("_history_store", _HISTORY_COLLECTION)
        self._knowledge_store._collection.count()
        self._history_store._collection.count()
        return True

    # ============ 知识文档索引 ============

    def _load_knowledge_documents(self) -> List[Document]:
        """读取 data/knowledge/*.md 并按段落切块"""
        documents: List[Document] = []
        for md_path in sorted(KNOWLEDGE_DIR.glob("*.md")):
            city = _CITY_NAME_MAP.get(md_path.stem, md_path.stem)
            content = md_path.read_text(encoding="utf-8")
            for index, chunk in enumerate(self._text_splitter.split_text(content)):
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "city": city,
                            "source": md_path.name,
                            "chunk_id": f"{md_path.name}:{index}",
                            "source_type": "markdown",
                        },
                    )
                )
        return documents

    def ensure_knowledge_index(self) -> bool:
        """确保知识索引存在 (空库时自动构建, 幂等)

        用集合 count 判断是否已有数据(不走嵌入API, 避免网络抖动误判),
        非空则跳过; 空库才触发构建(构建失败自动重试)。
        """
        if not self.enabled:
            return False
        self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
        try:
            if self._knowledge_store._collection.count() > 0:
                return True  # 已有数据, 无需重建
        except Exception:
            pass
        return self.build_knowledge_index()["success"]

    def build_knowledge_index(self, retries: int = 2) -> dict:
        """重建静态 Markdown 知识索引，不清除动态或审核发布的公共资料。

        网络瞬断(嵌入 API 偶发抖动)时自动重试 retries 次, 避免一次性失败留空库。
        公共图文资料和高德动态资料共享 collection，不能为重建 Markdown 而删除。
        """
         # ① RAG 没启用 → 直接报告"未启用"
        if not self.enabled:
            return {"success": False, "message": "RAG 未启用 (缺少嵌入配置)", "chunks": 0}
        self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
        #  ② 读取 knowledge/*.md 并切块
        documents = self._load_knowledge_documents()
        # ③ 目录为空 → 报告"知识目录为空"
        if not documents:
            return {"success": True, "message": "知识目录为空", "chunks": 0}

        import time as _time

        last_err = None
        for attempt in range(retries + 1):
            try:
                # ④ 仅替换静态 Markdown 块；保留 source_type=multimodal 和高德动态块。
                self._knowledge_store.delete(where={"source_type": "markdown"})
                 # ⑤ 全部向量化并写入
                with observe_rag_operation("knowledge_index_embedding"):
                    self._knowledge_store.add_documents(documents)
                logger.info(f"📚 静态知识索引重建完成: {len(documents)} 个文本块")
                return {"success": True, "message": f"已替换 {len(documents)} 个静态文本块", "chunks": len(documents)}
            except Exception as e:
                last_err = e
                if attempt < retries:
                    # 网络瞬断(嵌入API抖动)等可恢复错误, 退避重试
                    logger.warning(f"⚠️ 知识索引构建失败(第{attempt + 1}次), 重试中: {e}")
                    _time.sleep(2 * (attempt + 1))
        # ⑦ 多次失败 → 记录错误并返回失败信息
        logger.error(f"❌ 知识索引构建失败: {last_err}")
        return {"success": False, "message": str(last_err), "chunks": 0}

    # ============ 历史行程入库 ============

    def add_history_plan(
        self, record_id: int, user_id: int, request: TripRequest, trip_plan: TripPlan
    ) -> bool:
        """把一份行程计划写入私有历史向量库（检索时必须按 user_id 过滤）。"""
        if not self.enabled:
            return False
        try:
            self._refresh_store("_history_store", _HISTORY_COLLECTION)
            text = self._plan_to_text(request, trip_plan)
            self._history_store.add_documents(
                [
                    Document(
                        page_content=text,
                        metadata={"record_id": record_id, "user_id": user_id, "city": request.city},
                    )
                ],
                ids=[f"history-{user_id}-{record_id}"],
            )
            logger.info(f"🧠 历史行程已写入 RAG 向量库: record_id={record_id}, user_id={user_id}")
            return True
        except Exception as e:
            logger.warning(f"⚠️  历史行程入库失败: {e}")
            return False

    @staticmethod
    def _plan_to_text(request: TripRequest, trip_plan: TripPlan) -> str:
        """把行程计划转为可检索的摘要文本"""
        lines = [
            f"{request.city} {request.travel_days}天旅行计划",
            f"日期: {request.start_date} 至 {request.end_date}",
            f"交通: {request.transportation}, 住宿: {request.accommodation}",
            f"偏好: {','.join(request.preferences) if request.preferences else '无'}",
        ]
        for day in trip_plan.days:
            attractions = "、".join(a.name for a in day.attractions)
            lines.append(f"第{day.day_index + 1}天: {attractions}")
        if trip_plan.budget:
            lines.append(f"总预算: {trip_plan.budget.total}元")
        return "\n".join(lines)

    # ============ 检索 ============

    def delete_history_plan(self, record_id: int, user_id: int) -> bool:
        """删除历史记录时同步移除其私有向量，遵守数据删除语义。"""
        if not self.enabled:
            return False
        try:
            self._refresh_store("_history_store", _HISTORY_COLLECTION)
            self._history_store.delete(ids=[f"history-{user_id}-{record_id}"])
            return True
        except Exception as exc:
            logger.warning("历史向量删除失败: record_id=%s, error=%s", record_id, exc)
            return False

    def replace_public_knowledge_document(
        self, document_id: int, city: str, title: str, page_texts: List[str]
    ) -> bool:
        """以来源文档为单位替换公共图文知识，保留页码与可追溯元数据。"""
        if not self.enabled:
            return False
        try:
            self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
            self._knowledge_store.delete(where={"document_id": document_id})
            documents: List[Document] = []
            ids: List[str] = []
            for page, text in enumerate(page_texts, start=1):
                for index, chunk in enumerate(self._text_splitter.split_text(text)):
                    chunk_id = f"submission:{document_id}:{page}:{index}"
                    documents.append(Document(
                        page_content=chunk,
                        metadata={
                            "city": city,
                            "source": title,
                            "source_type": "multimodal",
                            "document_id": document_id,
                            "page": page,
                            "chunk_id": chunk_id,
                        },
                    ))
                    ids.append(chunk_id)
            if documents:
                with observe_rag_operation("multimodal_knowledge_embedding"):
                    self._knowledge_store.add_documents(documents, ids=ids)
            return True
        except Exception as exc:
            logger.warning("公共图文知识写入失败: document_id=%s, error=%s", document_id, exc)
            return False

    def delete_public_knowledge_document(self, document_id: int) -> bool:
        if not self.enabled:
            return False
        try:
            self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
            self._knowledge_store.delete(where={"document_id": document_id})
            return True
        except Exception as exc:
            logger.warning("公共图文知识删除失败: document_id=%s, error=%s", document_id, exc)
            return False

    def retrieve(
        self, query: str, city: Optional[str] = None, k: int = 3, user_id: Optional[int] = None
    ) -> List[str]:
        """检索城市知识及当前用户的历史；缺少用户身份时绝不检索历史。"""
        if not self.enabled:
            return []
        results: List[str] = []
        try:
            self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
            self._refresh_store("_history_store", _HISTORY_COLLECTION)
            # 城市知识与个人历史使用同一查询文本；只做一次远程嵌入，再在本地 Chroma
            # 对两个集合检索，避免一次规划产生两次相同的 embedding HTTP 请求。
            with observe_rag_operation("query_embedding"):
                query_embedding = self._embedding.embed_query(query)
            # 1. 城市知识库 (限定城市, 相关性最高)
            if city:
                with observe_rag_operation("knowledge_vector_search"):
                    docs = self._knowledge_store.similarity_search_by_vector(
                        query_embedding, k=k, filter={"city": city}
                    )
                for doc in docs:
                    source = doc.metadata.get("source", "未知来源")
                    page = doc.metadata.get("page")
                    source_label = f"{source} 第{page}页" if page else source
                    results.append(f"[知识库-{doc.metadata.get('city')} / {source_label}] {doc.page_content}")
            # 2. 历史行程（仅限当前用户；旧版无 user_id 的向量不会被命中）
            if user_id is not None:
                with observe_rag_operation("history_vector_search"):
                    docs = self._history_store.similarity_search_by_vector(
                        query_embedding, k=2, filter={"user_id": user_id}
                    )
                results.extend(f"[我的历史行程] {doc.page_content}" for doc in docs)
        except Exception as e:
            logger.warning(f"⚠️  RAG 检索失败: {e}")
        return results

    def build_rag_context(
        self, request: TripRequest, top_k: int = 3, user_id: Optional[int] = None
    ) -> str:
        """为规划请求构建 RAG 上下文文本 (注入 LLM prompt 用)"""
        if not self.enabled:
            return ""
        # 任意城市增强: 若该城市尚无高德自动入库数据, 先用高德实时搜索自动建知识
        with observe_rag_operation("context_build"):
            self.ensure_city_index(request.city)
            query = (
                f"{request.city} {request.travel_days}天旅行 "
                f"{','.join(request.preferences) if request.preferences else ''} "
                f"{request.free_text_input or ''}"
            )
            chunks = self.retrieve(query, city=request.city, k=top_k, user_id=user_id)
        if not chunks:
            return ""
        header = "## 检索到的相关知识 (供你参考, 让行程更真实/贴合当地实际):"
        return header + "\n" + "\n\n".join(f"- {c}" for c in chunks)

    def ensure_city_index(self, city: str) -> bool:
        """确保任意城市在知识库中有可检索数据 (幂等)。

        手写知识库(md)覆盖的城市直接用精选内容; 未覆盖的城市, 用高德实时搜索
        该城市热门景点并生成结构化知识文本, 写入知识库(带 source="gaode:<城市>" 标记)。
        这样任意城市查一次即获得增强, 且不会重复写入膨胀向量库。

        Returns:
            True 表示该城市知识已可用; False 表示写入失败或无需写入
        """
        if not self.enabled or not city:
            return False
        try:
            self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
            # 幂等: 该城市是否已有高德自动数据(或手写md数据)
            existing = self._knowledge_store.get(where={"source": f"{_GAODE_SOURCE_PREFIX}{city}"})
            if existing and existing.get("ids"):
                return True

            from ..services.amap_service import get_amap_service

            amap = get_amap_service()
            # 用「城市名 + 景点」搜索, 让高德返回偏旅游景点的POI(纯城市名会混入餐饮/住宿)
            with observe_rag_operation("city_index_build"):
                pois = amap.search_poi(f"{city}必去景点", city)
            if not pois:
                with observe_rag_operation("city_index_build"):
                    pois = amap.search_poi(city, city)
            if not pois:
                # 高德没搜到(输入可能是乡镇/国外等), 不强写, 退回现有检索
                return False

            # 过滤明显非景点的 POI (餐饮/住宿/购物等), 避免把餐馆当景点写进知识库
            _NON_ATTRACTION_TYPES = ("餐饮", "中餐厅", "餐厅", "酒店", "宾馆", "住宿", "购物", "超市", "银行", "KTV", "酒吧", "足疗", "洗浴")
            pois = [p for p in pois if not any(t in (p.type or "") for t in _NON_ATTRACTION_TYPES)]
            if not pois:
                return False

            # 生成该城市知识文本: 每个景点一段结构化信息(名称/地址/坐标/标签)
            chunks = []
            for poi in pois[:_GAODE_CITY_MAX_CHUNKS]:
                line = (
                    f"### {poi.name}\n"
                    f"- 地址: {poi.address or '不详'}\n"
                    f"- 坐标: {poi.location.longitude},{poi.location.latitude}\n"
                    f"- 类别: {poi.type or '景点'}"
                )
                chunks.append(Document(
                    page_content=line,
                    metadata={"city": city, "source": f"{_GAODE_SOURCE_PREFIX}{city}"},
                ))

            if chunks:
                with observe_rag_operation("city_index_embedding"):
                    self._knowledge_store.add_documents(chunks)
                logger.info(f"📍 任意城市增强: 已为「{city}」自动写入 {len(chunks)} 个景点知识块")
            return True
        except Exception as e:
            logger.warning(f"⚠️  任意城市增强失败(不影响主流程): {e}")
            return False

    def get_knowledge_attractions(self, city: str, max_names: int = 5) -> List[str]:
        """从知识库提取该城市知名景点名 (供补充进"可选景点"列表, 让LLM能真实采用)

        知识库景点带门票/交通/避坑信息, 但本身无坐标;
        返回景点名后由调用方用高德按名搜索补上真实坐标, 即可进入行程候选。
        """
        if not self.enabled:
            return []
        self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
        # 任意城市增强: 未覆盖城市先用高德自动建知识, 使景点补充也生效
        self.ensure_city_index(city)
        try:
            docs = self._knowledge_store.similarity_search(
                f"{city} 必去景点 门票 交通 打卡",
                k=3,
                filter={"city": city},
            )
            names: List[str] = []
            for doc in docs:
                for m in re.finditer(r"^###\s+(.+)$", doc.page_content, re.M):
                    name = m.group(1).strip()
                    if name and name not in names:
                        names.append(name)
            return names[:max_names]
        except Exception as e:
            logger.warning(f"⚠️  知识库景点提取失败: {e}")
            return []

    def get_attraction_rag_text(self, name: str, city: str, max_chars: int = 320) -> str:
        """检索知识库中某景点的详细信息 (门票/开放时间/交通/打卡/避坑)

        供行程生成后回填到景点描述, 让知识库内容真正落到前端每个景点上。
        只取最相关的一段, 避免把其他景点的内容拼进来。
        """
        return self.get_attraction_rag_texts([name], city, max_chars).get(name, "")

    def get_attraction_rag_texts(
        self, names: List[str], city: str, max_chars: int = 320
    ) -> dict[str, str]:
        """批量获取景点详情。

        先把所有景点查询一次性向量化，再用向量在 Chroma 中本地检索，避免 N 个景点
        串行发 N 次 embedding 请求而阻塞行程接口返回。
        """
        if not self.enabled:
            return {}
        self._refresh_store("_knowledge_store", _KNOWLEDGE_COLLECTION)
        unique_names = list(dict.fromkeys(name for name in names if name))
        if not unique_names:
            return {}
        try:
            queries = [f"{city} {name} 门票 开放时间 交通 避坑 打卡" for name in unique_names]
            with observe_rag_operation("attraction_detail_embedding"):
                embeddings = self._embedding.embed_documents(queries)
            details: dict[str, str] = {}
            for name, embedding in zip(unique_names, embeddings):
                docs = self._knowledge_store.similarity_search_by_vector(
                    embedding, k=1, filter={"city": city}
                )
                if not docs:
                    continue
                lines = []
                for line in docs[0].page_content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("##") or line.startswith("###"):
                        continue
                    lines.append(line)
                detail = "\n".join(lines).strip()[:max_chars]
                if detail:
                    if docs[0].metadata.get("source_type") == "multimodal":
                        source = docs[0].metadata.get("source", "公共攻略")
                        page = docs[0].metadata.get("page")
                        detail = f"[知识来源: {source}{f' 第{page}页' if page else ''}]\n{detail}"
                    details[name] = detail
            return details
        except Exception as e:
            logger.warning(f"⚠️  知识库景点详情批量检索失败: {e}")
            return {}


# 全局单例
_rag_service: Optional[RagService] = None


def get_rag_service() -> RagService:
    """获取 RAG 服务实例 (单例模式)"""
    global _rag_service
    if _rag_service is None:
        _rag_service = RagService()
    return _rag_service
