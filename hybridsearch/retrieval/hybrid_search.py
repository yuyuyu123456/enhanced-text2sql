"""HybridCypherSearch — combines Neo4j graph traversal with vector similarity.

Four retrieval strategies are run in parallel, then fused via Reciprocal Rank
Fusion (RRF) to produce a single ranked candidate list.
"""

import asyncio
from typing import Any

from hybridsearch.neo4j.client import Neo4jClient
from hybridsearch.neo4j.schema import (
    NODE_QUESTION,
    NODE_SQL,
    NODE_ENTITY,
    NODE_KEYWORD,
    NODE_TABLE,
    NODE_COLUMN,
    NODE_SQL_TEMPLATE,
    REL_HAS_SQL,
    REL_HAS_ENTITY,
    REL_HAS_KEYWORD,
    REL_SYNONYM_OF,
    REL_MAPS_TO,
    REL_HAS_COLUMN,
    REL_INSTANTIATES,
)

# ── SQL structural keywords for template matching ──
_STRUCTURAL_KEYWORDS = [
    "GROUP BY", "ORDER BY", "HAVING", "JOIN", "UNION",
    "INTERSECT", "EXCEPT", "LIMIT", "DISTINCT", "COUNT(",
    "SUM(", "AVG(", "MAX(", "MIN(", "WHERE",
]


class HybridCypherSearch:
    """Combined graph-traversal + vector-similarity search over the Neo4j graph.

    Four search dimensions:
    1. **Vector** — Neo4j native vector index on Question embeddings
    2. **Entities** — Entity → Column mapping → Table → Question traversal
    3. **Keywords** — Keyword match (including synonyms)
    4. **SQL Template** — Structural keyword match in parameterized templates

    Results are fused with Reciprocal Rank Fusion (k=60).
    """

    N_RESULTS_GRAPH = 15
    N_RESULTS_VECTOR = 15
    N_RESULTS_HYBRID = 15
    RRF_K = 60

    def __init__(self, client: Neo4jClient,
                 n_graph: int = 15, n_vector: int = 15, n_hybrid: int = 15):
        self.client = client
        self.N_RESULTS_GRAPH = n_graph
        self.N_RESULTS_VECTOR = n_vector
        self.N_RESULTS_HYBRID = n_hybrid

    # ── Individual search methods ──

    async def search_by_vector(
        self, embedding: list[float], n_results: int | None = None
    ) -> list[dict]:
        """Neo4j native vector index search on Question embeddings."""
        n = n_results or self.N_RESULTS_VECTOR
        if not embedding:
            return []

        try:
            records = await self.client.execute_read(
                """MATCH (node:Question)
                   SEARCH node IN (
                     VECTOR INDEX question_embedding_index
                     FOR $embedding
                     LIMIT $n
                   ) SCORE AS score
                   MATCH (node)-[:HAS_SQL]->(s:SQL)
                   RETURN node.text AS question, s.text AS sql, score
                   ORDER BY score DESC""",
                n=n,
                embedding=embedding,
            )
        except Exception:
            return []

        return [
            {"question": r.get("question", ""), "sql": r.get("sql", ""),
             "score": r.get("score", 0.0), "source": "vector"}
            for r in records
        ]

    async def search_by_entities(
        self, entities: list[str], n_results: int | None = None
    ) -> list[dict]:
        """Find questions via entity → column → table traversal."""
        n = n_results or self.N_RESULTS_GRAPH
        if not entities:
            return []

        results: dict[str, dict] = {}

        for entity in entities:
            try:
                records = await self.client.execute_read(
                    f"""MATCH (c:{NODE_COLUMN})-[:{REL_MAPS_TO}]->(e:{NODE_ENTITY} {{name: $entity}})
                        MATCH (t:{NODE_TABLE})-[:{REL_HAS_COLUMN}]->(c)
                        WITH t, collect(DISTINCT c.name) AS cols
                        MATCH (q:{NODE_QUESTION})-[:{REL_HAS_ENTITY}]->(e)
                        MATCH (q)-[:{REL_HAS_SQL}]->(s:{NODE_SQL})
                        RETURN q.text AS question, s.text AS sql,
                               t.name AS table, cols
                        LIMIT $n""",
                    entity=entity,
                    n=n,
                )
            except Exception:
                continue

            for r in records:
                q = r.get("question", "")
                if q not in results:
                    results[q] = {
                        "question": q,
                        "sql": r.get("sql", ""),
                        "tables": [],
                        "columns": [],
                        "source": "entity",
                        "score": 0.0,
                    }
                tbl = r.get("table", "")
                if tbl and tbl not in results[q]["tables"]:
                    results[q]["tables"].append(tbl)
                cols = r.get("cols", [])
                for c in cols:
                    if c not in results[q]["columns"]:
                        results[q]["columns"].append(c)

        # Score: +0.4 per matched table
        for item in results.values():
            item["score"] = min(1.0, len(item["tables"]) * 0.4)

        scored = sorted(results.values(), key=lambda x: x["score"], reverse=True)
        return scored[:n]

    async def search_by_keywords(
        self, keywords: list[str], n_results: int | None = None
    ) -> list[dict]:
        """Find questions by keyword match (including synonyms)."""
        n = n_results or self.N_RESULTS_GRAPH
        if not keywords:
            return []

        try:
            records = await self.client.execute_read(
                f"""MATCH (q:{NODE_QUESTION})-[:{REL_HAS_KEYWORD}]->(k:{NODE_KEYWORD})
                    WHERE k.word IN $keywords
                    OPTIONAL MATCH (k)-[:{REL_SYNONYM_OF}]->(syn:{NODE_KEYWORD})
                    MATCH (q)-[:{REL_HAS_SQL}]->(s:{NODE_SQL})
                    RETURN q.text AS question, s.text AS sql,
                           k.word AS matched_keyword,
                           collect(DISTINCT syn.word) AS synonyms
                    LIMIT $n""",
                keywords=keywords,
                n=n,
            )
        except Exception:
            return []

        results: dict[str, dict] = {}
        for r in records:
            q = r.get("question", "")
            if q not in results:
                results[q] = {
                    "question": q,
                    "sql": r.get("sql", ""),
                    "matched_keywords": [],
                    "synonyms": [],
                    "source": "keyword",
                    "score": 0.0,
                }
            kw = r.get("matched_keyword", "")
            if kw and kw not in results[q]["matched_keywords"]:
                results[q]["matched_keywords"].append(kw)
            syns = r.get("synonyms", []) or []
            for s in syns:
                if s and s not in results[q]["synonyms"]:
                    results[q]["synonyms"].append(s)

        # Score: +0.2 per keyword match
        for item in results.values():
            item["score"] = min(1.0, len(item["matched_keywords"]) * 0.2)

        scored = sorted(results.values(), key=lambda x: x["score"], reverse=True)
        return scored[:n]

    async def search_by_sql_template(
        self, question: str, n_results: int | None = None
    ) -> list[dict]:
        """Find questions whose SQL templates share structural keywords."""
        n = n_results or self.N_RESULTS_GRAPH

        # Extract structural clues from question text
        question_upper = question.upper()
        structural_clues = [kw for kw in _STRUCTURAL_KEYWORDS if kw in question_upper]

        if not structural_clues:
            return []

        results: dict[str, dict] = {}
        for clue in structural_clues:
            try:
                records = await self.client.execute_read(
                    f"""MATCH (st:{NODE_SQL_TEMPLATE})
                        WHERE st.template CONTAINS $clue
                        MATCH (s:{NODE_SQL})-[:{REL_INSTANTIATES}]->(st)
                        MATCH (q:{NODE_QUESTION})-[:{REL_HAS_SQL}]->(s)
                        RETURN q.text AS question, s.text AS sql,
                               st.template AS template
                        LIMIT $n""",
                    clue=clue,
                    n=n,
                )
            except Exception:
                continue

            for r in records:
                q = r.get("question", "")
                if q not in results:
                    results[q] = {
                        "question": q,
                        "sql": r.get("sql", ""),
                        "template": r.get("template", ""),
                        "matched_clues": [],
                        "source": "template",
                        "score": 0.0,
                    }
                if clue not in results[q]["matched_clues"]:
                    results[q]["matched_clues"].append(clue)

        for item in results.values():
            item["score"] = min(1.0, len(item["matched_clues"]) * 0.1)

        scored = sorted(results.values(), key=lambda x: x["score"], reverse=True)
        return scored[:n]

    # ── Full-text search ──

    async def search_by_fulltext_entities(
        self, query: str, n_results: int | None = None
    ) -> list[dict]:
        """Full-text search on Entity nodes by name."""
        n = n_results or self.N_RESULTS_GRAPH
        if not query:
            return []
        try:
            records = await self.client.execute_read(
                """CALL db.index.fulltext.queryNodes('entity_fulltext_index', $query)
                   YIELD node, score
                   WHERE node:Entity
                   MATCH (c:Column)-[:MAPS_TO]->(node)
                   MATCH (t:Table)-[:HAS_COLUMN]->(c)
                   RETURN node.name AS entity, node.source_table AS table,
                          collect(DISTINCT c.name) AS columns, score
                   ORDER BY score DESC LIMIT $n""",
                query=query, n=n,
            )
        except Exception:
            return []

        results = {}
        for r in records:
            key = r.get("entity", "")
            if key not in results:
                results[key] = {
                    "entity": key,
                    "table": r.get("table", ""),
                    "columns": r.get("columns", []),
                    "score": r.get("score", 0.0),
                    "source": "fulltext_entity",
                }
        return sorted(results.values(), key=lambda x: x["score"], reverse=True)[:n]

    async def search_by_fulltext_values(
        self, query: str, n_results: int | None = None
    ) -> list[dict]:
        """Full-text search on Value nodes by meaning."""
        n = n_results or self.N_RESULTS_GRAPH
        if not query:
            return []
        try:
            records = await self.client.execute_read(
                """CALL db.index.fulltext.queryNodes('value_fulltext_index', $query)
                   YIELD node, score
                   WHERE node:Value
                   MATCH (c:Column)-[:HAS_VALUE]->(node)
                   MATCH (t:Table)-[:HAS_COLUMN]->(c)
                   RETURN node.value AS value, node.meaning AS meaning, node.synonyms AS synonyms,
                          c.name AS column, t.name AS table, score
                   ORDER BY score DESC LIMIT $n""",
                query=query, n=n,
            )
        except Exception:
            return []

        results = {}
        for r in records:
            key = f"{r.get('table', '')}.{r.get('column', '')}.{r.get('value', '')}"
            if key not in results:
                results[key] = {
                    "value": r.get("value", ""),
                    "meaning": r.get("meaning", ""),
                    "synonyms": r.get("synonyms", []),
                    "column": r.get("column", ""),
                    "table": r.get("table", ""),
                    "score": r.get("score", 0.0),
                    "source": "fulltext_value",
                }
        return sorted(results.values(), key=lambda x: x["score"], reverse=True)[:n]

    # ── Vector search on descriptions ──

    async def search_by_vector_table_desc(
        self, embedding: list[float], n_results: int | None = None
    ) -> list[dict]:
        """Vector search on Table description embeddings."""
        n = n_results or self.N_RESULTS_GRAPH
        if not embedding:
            return []
        try:
            records = await self.client.execute_read(
                """MATCH (t:Table)
                   SEARCH t IN (
                     VECTOR INDEX table_desc_embedding_index
                     FOR $embedding
                     LIMIT $n
                   ) SCORE AS score
                   WHERE t.description_embedding IS NOT NULL
                   RETURN t.name AS table, t.summary AS summary, score
                   ORDER BY score DESC""",
                n=n, embedding=embedding,
            )
        except Exception:
            return []
        return [
            {"table": r.get("table", ""), "summary": r.get("summary", ""),
             "score": r.get("score", 0.0), "source": "vector_table_desc"}
            for r in records
        ]

    async def search_by_vector_column_desc(
        self, embedding: list[float], n_results: int | None = None
    ) -> list[dict]:
        """Vector search on Column description embeddings."""
        n = n_results or self.N_RESULTS_GRAPH
        if not embedding:
            return []
        try:
            records = await self.client.execute_read(
                """MATCH (c:Column)
                   SEARCH c IN (
                     VECTOR INDEX column_desc_embedding_index
                     FOR $embedding
                     LIMIT $n
                   ) SCORE AS score
                   WHERE c.description_embedding IS NOT NULL
                   RETURN c.name AS column, c.table AS table,
                          c.description AS description, score
                   ORDER BY score DESC""",
                n=n, embedding=embedding,
            )
        except Exception:
            return []
        return [
            {"column": r.get("column", ""), "table": r.get("table", ""),
             "description": r.get("description", ""),
             "score": r.get("score", 0.0), "source": "vector_column_desc"}
            for r in records
        ]

    # ── Hybrid fusion ──

    async def _search_graph(
        self, entities: list[str], keywords: list[str], question: str
    ) -> list[dict]:
        """Run all three graph-based searches in parallel."""
        results = await asyncio.gather(
            self.search_by_entities(entities),
            self.search_by_keywords(keywords),
            self.search_by_sql_template(question),
        )
        # Flatten
        all_results: list[dict] = []
        for batch in results:
            all_results.extend(batch)
        return all_results

    async def hybrid_search(
        self,
        question: str,
        entities: list[str] | None = None,
        keywords: list[str] | None = None,
        embedding: list[float] | None = None,
    ) -> list[dict]:
        """Primary entry point — run graph + vector searches and fuse with RRF.

        Args:
            question: The user's natural language question.
            entities: List of entity names extracted from the question.
            keywords: List of keyword strings extracted from the question.
            embedding: Pre-computed embedding for the question.

        Returns:
            Top N_RESULTS_HYBRID candidates sorted by fused RRF score.
        """
        entities = entities or []
        keywords = keywords or []

        # Run all search dimensions in parallel
        graph_task = self._search_graph(entities, keywords, question)
        vector_q_task = self.search_by_vector(embedding or [])
        vector_tbl_task = self.search_by_vector_table_desc(embedding or [])
        vector_col_task = self.search_by_vector_column_desc(embedding or [])
        ft_entity_task = self.search_by_fulltext_entities(question)
        ft_value_task = self.search_by_fulltext_values(question)

        results_list = await asyncio.gather(
            graph_task, vector_q_task, vector_tbl_task, vector_col_task,
            ft_entity_task, ft_value_task,
        )

        all_raw_results: list[list[dict]] = results_list

        # ── Reciprocal Rank Fusion (all sources) ──
        scored: dict[str, dict] = {}

        def _rrf_score(rank: int) -> float:
            return 1.0 / (self.RRF_K + rank)

        for batch in all_raw_results:
            for rank, item in enumerate(batch, start=1):
                # Use question as key for SQL-carrying results; fall back to entity/value/table key
                key = item.get("question") or item.get("entity") or item.get("value") or item.get("table", "")
                if not key:
                    continue
                if key not in scored:
                    scored[key] = dict(item)
                    scored[key]["rrf_score"] = 0.0
                    scored[key]["sources"] = [item.get("source", "unknown")]
                scored[key]["rrf_score"] += _rrf_score(rank)
                src = item.get("source", "unknown")
                if src not in scored[key]["sources"]:
                    scored[key]["sources"].append(src)

        # Sort by RRF score descending, take top N
        fused = sorted(scored.values(), key=lambda x: x["rrf_score"], reverse=True)
        return fused[:self.N_RESULTS_HYBRID]
