"""本地 SQLite 零配置启动的向后兼容测试。"""

from sqlalchemy import create_engine, inspect, text

from app.db import database as dbmod


def test_ensure_tables_upgrades_legacy_sqlite_knowledge_document(monkeypatch, tmp_path):
    """旧 SQLite 已有知识表时也必须补上来源等级列。"""
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE knowledge_documents (id INTEGER PRIMARY KEY, title VARCHAR(160))"))

    old_engine, old_url, old_ready = dbmod.engine, dbmod.database_url, dbmod._tables_ready
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "database_url", "sqlite:///legacy.db")
    monkeypatch.setattr(dbmod, "_tables_ready", False)
    try:
        dbmod.ensure_tables()
        assert "source_tier" in {column["name"] for column in inspect(engine).get_columns("knowledge_documents")}
    finally:
        dbmod.engine, dbmod.database_url, dbmod._tables_ready = old_engine, old_url, old_ready
