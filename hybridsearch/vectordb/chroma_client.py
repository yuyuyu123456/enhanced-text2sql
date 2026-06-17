"""ChromaDB client for hybridsearch — manages business document collections.

Two collections:
- ``tables_document`` — per-table business docs (table_*.md)
- ``business_document`` — cross-cutting docs (enum_values, address_pattern, global_metrics)

Independent from t2sql's ChromaDB — runs its own persistent client in
``hybridsearch/vector_db_storage/``.
"""

import asyncio
import json
import os
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction


_COLLECTION_TABLES = "tables_document"
_COLLECTION_BUSINESS = "business_document"


class HybridChromaClient:
    """ChromaDB client for hybridsearch business documentation."""

    def __init__(self, persist_dir: str | None = None):
        if persist_dir is None:
            persist_dir = os.path.join(os.path.dirname(__file__), "..", "vector_db_storage")
        os.makedirs(persist_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_dir)

        # Embedding function — reuse same config as t2sql
        api_key, api_base, model_name = self._read_embedding_config()
        self._embedding_fn = OpenAIEmbeddingFunction(
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
        )

        # Collections
        self._tables = self._client.get_or_create_collection(
            name=_COLLECTION_TABLES,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self._business = self._client.get_or_create_collection(
            name=_COLLECTION_BUSINESS,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _read_embedding_config() -> tuple[str, str | None, str]:
        """Read embedding config from the t2sql descriptor (same as Neo4j client)."""
        api_key = ""
        api_base = None
        model_name = "text-embedding-ada-002"
        try:
            from t2sql.utils import get_config
            config = get_config()
            api_key = config.get("embedding_api_key") or config.get("open_ai_key") or ""
            api_base = config.get("embedding_api_base")
            model_name = config.get("embedding_model_name", "text-embedding-ada-002")
        except Exception:
            api_key = os.environ.get("EMBEDDING_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        return api_key, api_base, model_name

    # ── Ingest ──

    def add_table_doc(self, table_name: str, content: str) -> None:
        """Add or update a per-table business document."""
        doc_id = f"table_{table_name}"
        self._tables.upsert(
            documents=[content],
            metadatas=[{"table": table_name, "doc_type": "table_business_doc"}],
            ids=[doc_id],
        )

    def add_business_doc(self, doc_name: str, content: str) -> None:
        """Add or update a cross-cutting business document."""
        doc_id = f"biz_{doc_name}"
        self._business.upsert(
            documents=[content],
            metadatas=[{"name": doc_name, "doc_type": "business_doc"}],
            ids=[doc_id],
        )

    # ── Query ──

    def query_tables(self, query: str, n_results: int = 5) -> list[dict]:
        """Vector search in tables_document collection."""
        result = self._tables.query(query_texts=[query], n_results=n_results)
        docs = []
        if result.get("documents") and result["documents"][0]:
            for i, doc in enumerate(result["documents"][0]):
                meta = (result.get("metadatas") or [[]])[0]
                dist = (result.get("distances") or [[]])[0]
                docs.append({
                    "document": doc,
                    "metadata": meta[i] if i < len(meta) else {},
                    "distance": dist[i] if i < len(dist) else 1.0,
                    "collection": "tables_document",
                })
        return docs

    def query_business(self, query: str, n_results: int = 5) -> list[dict]:
        """Vector search in business_document collection."""
        result = self._business.query(query_texts=[query], n_results=n_results)
        docs = []
        if result.get("documents") and result["documents"][0]:
            for i, doc in enumerate(result["documents"][0]):
                meta = (result.get("metadatas") or [[]])[0]
                dist = (result.get("distances") or [[]])[0]
                docs.append({
                    "document": doc,
                    "metadata": meta[i] if i < len(meta) else {},
                    "distance": dist[i] if i < len(dist) else 1.0,
                    "collection": "business_document",
                })
        return docs

    async def query_async(self, query: str, n_results: int = 5) -> dict:
        """Run both collection queries in parallel and return merged results."""
        tables, biz = await asyncio.gather(
            asyncio.to_thread(self.query_tables, query, n_results),
            asyncio.to_thread(self.query_business, query, n_results),
        )
        return {"tables_docs": tables, "business_docs": biz}
