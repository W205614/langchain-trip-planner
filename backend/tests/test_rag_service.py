"""RAG 服务测试: 维度自愈 + 任意城市动态增强

用 mock 避免真实高德/嵌入网络调用, 并隔离临时 Chroma 目录。
"""
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document


class FakeEmbedding:
    """假的嵌入对象: 返回固定维度向量, 供维度校验测试"""

    def __init__(self, dim=3072):
        self.dim = dim
        self.model = "text-embedding-3-large"

    def embed_query(self, text):
        return [0.1] * self.dim

    def embed_documents(self, texts):
        return [[0.1] * self.dim for _ in texts]


@pytest.fixture()
def rag(tmp_path, monkeypatch):
    """隔离临时 Chroma 目录的 RagService"""
    from app.services import rag_service as rag_mod

    monkeypatch.setattr(rag_mod, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(rag_mod, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    # 复用真实 Chroma + 假嵌入, 不调网络
    svc = rag_mod.RagService.__new__(rag_mod.RagService)  # 跳过 __init__ 的真实初始化
    svc.settings = MagicMock()
    svc._embedding = FakeEmbedding(dim=3072)
    svc._chroma_dir = tmp_path / "chroma"  # 供测试重连用

    from langchain_chroma import Chroma
    (tmp_path / "chroma").mkdir(parents=True, exist_ok=True)
    # store 的嵌入函数用独立实例, 避免测试里改 svc._embedding 时被 store 沿用
    svc._knowledge_store = Chroma(
        collection_name="trip_knowledge",
        embedding_function=FakeEmbedding(dim=3072),
        persist_directory=str(tmp_path / "chroma"),
    )
    svc._history_store = Chroma(
        collection_name="trip_history",
        embedding_function=svc._embedding,
        persist_directory=str(tmp_path / "chroma"),
    )
    svc._text_splitter = rag_mod.RecursiveCharacterTextSplitter(
        chunk_size=300, chunk_overlap=50,
        separators=["\n## ", "\n### ", "\n- ", "\n", "。", "；", " "],
    )
    return svc


def test_ensure_collections_consistent_same_dim(rag):
    """维度一致时不应重建集合"""
    # 预写一条 3072 维数据
    from langchain_core.documents import Document
    rag._knowledge_store.add_documents([
        Document(page_content="北京故宫", metadata={"city": "北京", "source": "beijing.md"})
    ])
    with patch.object(rag, "_embedding") as emb:
        emb.embed_query.return_value = [0.1] * 3072
        rag._ensure_collections_consistent()
    # 数据仍在 (未被清空)
    assert rag._knowledge_store._collection.count() == 1


def test_ensure_collections_consistent_dim_mismatch_rebuilds(rag, tmp_path):
    """维度不一致时应清空重建"""
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

    # 用 1024 维的独立 store 预写一条旧数据 (模拟旧嵌入模型写入)
    old_store = Chroma(
        collection_name="trip_knowledge",
        embedding_function=FakeEmbedding(dim=1024),
        persist_directory=str(tmp_path / "chroma"),
    )
    old_store.add_documents([
        Document(page_content="旧维度数据", metadata={"city": "北京", "source": "old.md"})
    ])
    assert old_store._collection.count() == 1

    # 当前嵌入是 3072 维 → 触发维度校验 → 应清空重建
    rag._ensure_collections_consistent()

    # 用 3072 维重连, 旧数据应不存在
    fresh = Chroma(
        collection_name="trip_knowledge",
        embedding_function=FakeEmbedding(dim=3072),
        persist_directory=str(tmp_path / "chroma"),
    )
    assert fresh._collection.count() == 0


def test_ensure_city_index_idempotent(rag, monkeypatch):
    """动态城市增强应幂等: 同一城市只写一次"""
    from app.services import rag_service as rag_mod
    fake_amap = MagicMock()
    fake_poi = MagicMock()
    fake_poi.name = "都江堰"
    fake_poi.address = "公园路"
    fake_poi.type = "风景名胜"
    fake_poi.location = MagicMock(longitude=103.6, latitude=30.99)
    fake_amap.search_poi.return_value = [fake_poi, fake_poi]  # 2个景点

    monkeypatch.setattr(rag_mod, "_GAODE_CITY_MAX_CHUNKS", 10)
    # patch amap_service 模块的 get_amap_service (rag_service 内部 import 它)
    with patch("app.services.amap_service.get_amap_service", return_value=fake_amap):
        ok1 = rag.ensure_city_index("成都")
        ok2 = rag.ensure_city_index("成都")  # 第二次应直接命中, 不再写

    assert ok1 is True
    assert ok2 is True
    # 只写了一次 (2个POI → 2块)
    src = rag._knowledge_store.get(where={"source": "gaode:成都"})
    assert len(src.get("ids", [])) == 2
    # 且 search_poi 只被调用一次 (幂等)
    assert fake_amap.search_poi.call_count == 1


def test_ensure_city_index_no_poi_returns_false(rag, monkeypatch):
    """高德搜不到 POI 时不应强写知识库"""
    from app.services import rag_service as rag_mod
    fake_amap = MagicMock()
    fake_amap.search_poi.return_value = []
    with patch("app.services.amap_service.get_amap_service", return_value=fake_amap):
        ok = rag.ensure_city_index("华盛顿")
    assert ok is False


def test_retrieve_embeds_shared_query_once(rag):
    """城市知识与个人历史检索应复用同一条查询向量。"""
    embedding = MagicMock()
    embedding.embed_query.return_value = [0.1] * 3072
    rag._embedding = embedding
    rag._knowledge_store = MagicMock()
    rag._history_store = MagicMock()
    rag._knowledge_store.similarity_search_by_vector.return_value = [
        Document(page_content="故宫信息", metadata={"city": "北京", "source": "beijing.md"})
    ]
    rag._history_store.similarity_search_by_vector.return_value = [
        Document(page_content="我的历史", metadata={})
    ]

    results = rag.retrieve("北京 两天旅行", city="北京", user_id=7)

    embedding.embed_query.assert_called_once_with("北京 两天旅行")
    assert rag._knowledge_store.similarity_search_by_vector.call_count == 1
    assert rag._history_store.similarity_search_by_vector.call_count == 1
    assert len(results) == 2


def test_delete_history_plan_removes_only_targeted_vector(rag):
    """删除历史记录必须调用用户和记录号组成的稳定向量 ID。"""
    store = MagicMock()
    rag._history_store = store

    assert rag.delete_history_plan(record_id=42, user_id=7) is True

    store.delete.assert_called_once_with(ids=["history-7-42"])


def test_delete_history_plan_returns_false_when_vector_store_fails(rag):
    rag._history_store = MagicMock()
    rag._history_store.delete.side_effect = RuntimeError("collection unavailable")

    assert rag.delete_history_plan(record_id=42, user_id=7) is False


def test_batch_attraction_details_embeds_once(rag):
    """多个景点详情应批量嵌入，而非每个景点单独请求外部服务。"""
    embedding = MagicMock()
    embedding.embed_documents.return_value = [[0.1] * 3072, [0.2] * 3072]
    rag._embedding = embedding
    rag._knowledge_store = MagicMock()
    rag._knowledge_store.similarity_search_by_vector.return_value = [
        Document(page_content="### 故宫\n- 门票: 60元\n- 开放时间: 08:30", metadata={})
    ]

    details = rag.get_attraction_rag_texts(["故宫", "天坛", "故宫"], "北京")

    embedding.embed_documents.assert_called_once_with([
        "北京 故宫 门票 开放时间 交通 避坑 打卡",
        "北京 天坛 门票 开放时间 交通 避坑 打卡",
    ])
    assert rag._knowledge_store.similarity_search_by_vector.call_count == 2
    assert details["故宫"] == "- 门票: 60元\n- 开放时间: 08:30"
    assert details["天坛"] == "- 门票: 60元\n- 开放时间: 08:30"


def test_readiness_reconnects_a_stale_chroma_collection_handle(rag, monkeypatch):
    stale = MagicMock()
    stale._collection.count.side_effect = RuntimeError("collection does not exist")
    fresh = MagicMock()
    fresh._collection.count.return_value = 0
    rag._knowledge_store = stale
    monkeypatch.setattr(rag, "_new_store", lambda _name: fresh)

    assert rag.is_ready() is True
    assert rag._knowledge_store is fresh
