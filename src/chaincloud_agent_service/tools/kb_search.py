"""Optional pgvector knowledge-base search tool migrated from chain_bot."""

from __future__ import annotations

import json
import os

import psycopg
from langchain_core.tools import StructuredTool


def _embed(text: str) -> list[float]:
    from sentence_transformers import SentenceTransformer

    model_name = os.environ.get("KB_EMBED_MODEL", "all-mpnet-base-v2")
    if not hasattr(_embed, "_model"):
        _embed._model = SentenceTransformer(model_name)  # type: ignore[attr-defined]
    return _embed._model.encode(text).tolist()  # type: ignore[attr-defined]


def search_knowledge(dsn: str, query: str, top_k: int = 5) -> str:
    if os.environ.get("KB_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        return json.dumps(
            {"results": [], "note": "知识库未启用，跳过检索"}, ensure_ascii=False
        )
    table = os.environ.get("KB_TABLE", "chain_data.chain_bot_knowledge")
    try:
        embedding = _embed(query)
        sql = f"""
            SELECT content, source, 1 - (embedding <=> %s::vector) AS similarity
            FROM {table}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(sql, (str(embedding), str(embedding), top_k)).fetchall()
        results = [
            {"content": row[0], "source": row[1], "similarity": round(float(row[2]), 4)}
            for row in rows
        ]
        return json.dumps(
            {"results": results, "count": len(results)}, ensure_ascii=False
        )
    except Exception as e:
        return json.dumps(
            {"results": [], "count": 0, "note": f"知识库向量检索暂不可用，已跳过：{e}"},
            ensure_ascii=False,
        )


def make_kb_search_tool(dsn: str) -> StructuredTool:
    def _invoke(query: str, top_k: int = 5) -> str:
        return search_knowledge(dsn, query, top_k)

    return StructuredTool.from_function(
        name="search_knowledge",
        description="在 pgvector 知识库中搜索相关背景知识；仅当 KB_ENABLED=true 时返回结果。",
        func=_invoke,
    )
