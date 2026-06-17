"""Neo4j async client with vector index and constraint management.

Reuses the same OpenAI embedding function as the existing ChromaDB vector store
to keep both systems in the same embedding space.
"""

import asyncio
import os
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

from hybridsearch.neo4j.schema import (
    NODE_QUESTION,
    NODE_TABLE,
    NODE_ENTITY,
    NODE_KEYWORD,
)


class Neo4jClient:
    """Async Neo4j client for the hybrid search graph.

    Manages connections, schema initialization (vector index + constraints),
    and provides read/write transaction helpers.

    Uses the same embedding function as ChromaDB so vectors from both systems
    are directly comparable (shared 1536-d OpenAI embedding space).
    """

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ):
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.password = password or os.environ.get("NEO4J_PASSWORD", "password")
        self._driver: AsyncDriver | None = None
        self._embedding_fn = None

    @property
    def driver(self) -> AsyncDriver:
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self.uri, auth=(self.user, self.password)
            )
        return self._driver

    @property
    def embedding_function(self):
        """Lazy-init embedding function matching ChromaDB's config.

        Reads from the t2sql descriptor config (like chromadb.py does)
        so embeddings are in the same vector space.
        """
        if self._embedding_fn is None:
            from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

            # Read from descriptor config (same as chromadb.py line 21-24)
            api_key = ""
            api_base = None
            model_name = "text-embedding-ada-002"

            try:
                from t2sql.utils import get_config
                config = get_config()
                api_key = config.get("embedding_api_key", config.get("open_ai_key", ""))
                api_base = config.get("embedding_api_base")
                model_name = config.get("embedding_model_name", "text-embedding-ada-002")
            except Exception:
                api_key = os.environ.get("EMBEDDING_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

            self._embedding_fn = OpenAIEmbeddingFunction(
                api_key=api_key,
                api_base=api_base,
                model_name=model_name,
            )
        return self._embedding_fn

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate an embedding for *text* using the shared embedding function."""
        fn = self.embedding_function
        import numpy as np
        result = await asyncio.to_thread(fn, [text])
        # OpenAIEmbeddingFunction returns list[list[float]] or list[ndarray]
        if isinstance(result, list) and len(result) > 0:
            val = result[0]
            if isinstance(val, np.ndarray):
                return val.tolist()
            if isinstance(val, list):
                return val
        return []

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def verify_connectivity(self) -> bool:
        try:
            await self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    # ── Transaction helpers ──

    async def execute_write(self, cypher: str, **params) -> Any:
        """Execute a write transaction (single-statement convenience)."""
        async with self.driver.session() as session:
            async def _tx(tx):
                result = await tx.run(cypher, **params)
                return await result.data()
            return await session.execute_write(_tx)

    async def execute_read(self, cypher: str, **params) -> Any:
        """Execute a read transaction (single-statement convenience)."""
        async with self.driver.session() as session:
            async def _tx(tx):
                result = await tx.run(cypher, **params)
                return await result.data()
            return await session.execute_read(_tx)

    # ── Schema initialization ──

    async def create_vector_index(self, dimensions: int = 2048) -> None:
        """Create a Neo4j native vector index on Question embeddings.

        The index uses cosine similarity. Dimension is auto-detected or defaults
        to 2048 (embedding-3).
        """
        # Drop existing index if dimension changed
        try:
            await self.driver.execute_query("DROP INDEX question_embedding_index IF EXISTS")
        except Exception:
            pass
        try:
            await self.driver.execute_query(
                f"""
                CREATE VECTOR INDEX question_embedding_index IF NOT EXISTS
                FOR (q:Question) ON (q.embedding)
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {dimensions},
                        `vector.similarity_function`: 'cosine'
                    }}
                }}
                """
            )
        except Exception as e:
            print(f"Warning: Could not create vector index: {e}")

    async def create_constraints(self) -> None:
        """Create uniqueness constraints on key node types."""
        constraints = [
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{NODE_TABLE}) REQUIRE n.name IS UNIQUE",
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{NODE_ENTITY}) REQUIRE n.name IS UNIQUE",
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{NODE_KEYWORD}) REQUIRE n.word IS UNIQUE",
        ]
        for cypher in constraints:
            try:
                await self.driver.execute_query(cypher)
            except Exception:
                pass

    async def create_description_vector_indexes(self, dimensions: int = 2048) -> None:
        """Create vector indexes on Table.description_embedding and Column.description_embedding."""
        for label, prop in [("Table", "description_embedding"), ("Column", "description_embedding")]:
            idx_name = f"{label.lower()}_desc_embedding_index"
            try:
                await self.driver.execute_query(f"DROP INDEX {idx_name} IF EXISTS")
            except Exception:
                pass
            try:
                await self.driver.execute_query(
                    f"""
                    CREATE VECTOR INDEX {idx_name} IF NOT EXISTS
                    FOR (n:{label}) ON (n.{prop})
                    OPTIONS {{
                        indexConfig: {{
                            `vector.dimensions`: {dimensions},
                            `vector.similarity_function`: 'cosine'
                        }}
                    }}
                    """
                )
            except Exception as e:
                print(f"Warning: Could not create {label} vector index: {e}")

    async def create_fulltext_indexes(self) -> None:
        """Create full-text indexes on Entity.name and Value.meaning for keyword search."""
        for label, prop in [("Entity", "name"), ("Value", "meaning")]:
            idx_name = f"{label.lower()}_fulltext_index"
            try:
                await self.driver.execute_query(f"DROP INDEX {idx_name} IF EXISTS")
            except Exception:
                pass
            try:
                await self.driver.execute_query(
                    f"CREATE FULLTEXT INDEX {idx_name} IF NOT EXISTS "
                    f"FOR (n:{label}) ON EACH [n.{prop}]"
                )
            except Exception as e:
                print(f"Warning: Could not create {label} fulltext index: {e}")

    async def populate_description_embeddings(self) -> None:
        """Generate and store embeddings for Table and Column descriptions."""
        # Tables
        tables = await self.execute_read(
            "MATCH (t:Table) WHERE t.summary IS NOT NULL AND t.description_embedding IS NULL "
            "RETURN t.name AS name, t.summary AS summary, t.purpose AS purpose"
        )
        for t in tables:
            text = f"{t['name']}. {(t.get('summary') or '')}. {(t.get('purpose') or '')}"
            emb = await self.generate_embedding(text)
            if emb and len(emb) > 10:
                await self.execute_write(
                    "MATCH (t:Table {name: $name}) SET t.description_embedding = $emb",
                    name=t["name"], emb=[float(x) for x in emb],
                )
        print(f"Table description embeddings: {len(tables)}")

        # Columns
        cols = await self.execute_read(
            "MATCH (c:Column) WHERE c.description IS NOT NULL AND c.description_embedding IS NULL "
            "RETURN c.col_key AS col_key, c.name AS name, c.table AS table, c.description AS desc, c.data_type AS dtype"
        )
        for c in cols:
            text = f"{c['table']}.{c['name']} ({c.get('dtype') or 'VARCHAR'}): {c.get('desc') or ''}"
            emb = await self.generate_embedding(text)
            if emb and len(emb) > 10:
                await self.execute_write(
                    "MATCH (c:Column {col_key: $col_key}) SET c.description_embedding = $emb",
                    col_key=c["col_key"], emb=[float(x) for x in emb],
                )
        print(f"Column description embeddings: {len(cols)}")

    async def initialize_schema(self) -> None:
        """Create all indexes and constraints."""
        await self.create_vector_index()
        await self.create_description_vector_indexes()
        await self.create_fulltext_indexes()
        await self.create_constraints()

    async def clear_all(self) -> None:
        """Delete every node and relationship (full reset)."""
        await self.driver.execute_query("MATCH (n) DETACH DELETE n")
