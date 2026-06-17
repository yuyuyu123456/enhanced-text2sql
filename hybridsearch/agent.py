"""HybridText2SQLAgent — Neo4j graph-native SQL generation.

Uses hybrid search (vector + fulltext + graph) for retrieval, then builds
a comprehensive prompt from all semantic data in the graph — table/column
descriptions, entities, enum values, SQL examples — and generates SQL
via the LiteLLM router directly.
"""

import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from t2sql.utils import logger, parse_code

from hybridsearch.neo4j.client import Neo4jClient
from hybridsearch.vectordb.chroma_client import HybridChromaClient
from hybridsearch.retrieval.hybrid_search import HybridCypherSearch
from hybridsearch.retrieval.reranker import Reranker
from hybridsearch.retrieval.reflection import ReflectionNode


# ── SQL cleaning ──

import re as _re

_ID_TO_NAME_MAP = {
    "staff": ("staff_id", "staff_name"),
    "customers": ("customer_id", "customer_name"),
    "suppliers": ("supplier_id", "supplier_name"),
    "products": ("product_id", "product_name"),
    "departments": ("department_id", "department_name"),
    "department_stores": ("dept_store_id", "store_name"),
    "department_store_chain": ("dept_store_chain_id", "dept_store_chain_name"),
}


def _prefer_human_readable_columns(sql: str, question: str) -> str:
    """Replace bare ID columns with their human-readable name counterparts.

    Triggered when the question asks 'which', 'who', 'names of', 'name of'.
    Example: SELECT staff_id → SELECT staff_name  (when asking 'which staff')
    Does NOT replace when the question explicitly asks for 'id' or 'ids'.
    """
    import re as _re3
    q_lower = question.lower()

    # Check if this is a human-facing question
    human_patterns = ["which staff", "which customer", "which supplier",
                      "which product", "which department", "who ",
                      "names of", "name of", "list the names"]
    asks_id = any(kw in q_lower for kw in ["id and name", "ids and names",
                                             "what are the id", "return the id",
                                             "give the id", "product id"])
    if asks_id or not any(p in q_lower for p in human_patterns):
        return sql

    # Replace bare ID selects with name columns
    for tbl_alias, (id_col, name_col) in _ID_TO_NAME_MAP.items():
        alias_variants = [
            f"{tbl_alias[0].upper()}.{id_col}",
            f"{tbl_alias}.{id_col}",
            f"{tbl_alias.upper()}.{id_col}",
            id_col,
        ]
        for variant in alias_variants:
            # Only replace in SELECT clause (before FROM)
            parts = _re3.split(r'\bFROM\b', sql, maxsplit=1, flags=_re3.IGNORECASE)
            if len(parts) == 2:
                select_part = parts[0]
                # Check if name_col is already in SELECT
                if name_col.lower() in select_part.lower():
                    continue  # Already has the name, skip
                # Replace id with name in SELECT only
                new_select = _re3.sub(
                    rf'\b{_re3.escape(variant)}\b',
                    name_col,
                    select_part,
                    count=1,
                    flags=_re3.IGNORECASE,
                )
                if new_select != select_part:
                    sql = f"{new_select} FROM {parts[1]}"
                    break
    return sql


def _remove_unused_joins(sql: str) -> str:
    """Remove JOINed tables whose columns aren't referenced in SELECT/WHERE/GROUP BY.

    Example: SELECT c.name FROM customers c JOIN addresses a ON ... WHERE c.x=1
    → a is never referenced → remove the JOIN.
    """
    import re as _re2
    upper = sql.upper()

    # Find all table aliases defined in JOINs
    join_aliases = {}
    for m in _re2.finditer(
        r'\bJOIN\s+(\w+)(?:\s+AS\s+)?(\w+)?\s+ON\s+(.+?)(?=\b(?:JOIN|WHERE|GROUP|ORDER|HAVING|LIMIT|$))',
        sql, _re2.IGNORECASE | _re2.DOTALL,
    ):
        tbl = m.group(1).lower()
        alias = (m.group(2) or tbl).lower()
        join_aliases[alias] = {
            "table": tbl,
            "full_match": m.group(0),
        }

    if not join_aliases:
        return sql

    # Collect all column references in SELECT, WHERE, GROUP BY, ORDER BY
    # (everything except the FROM/JOIN clauses)
    non_from_part = _re2.sub(
        r'\bFROM\b\s+.+', '', sql, count=1, flags=_re2.IGNORECASE | _re2.DOTALL,
    )

    # For each JOIN alias, check if it's referenced (alias.column or just alias)
    unused = []
    for alias, info in join_aliases.items():
        # Check if alias appears outside the JOIN clause itself
        pattern = _re2.compile(rf'\b{_re2.escape(alias)}\b', _re2.IGNORECASE)
        join_text = info["full_match"]
        rest_of_sql = sql.replace(join_text, "")
        if not pattern.search(rest_of_sql):
            unused.append(info["full_match"])

    # Remove unused JOINs
    result = sql
    for join_text in unused:
        result = result.replace(join_text, "")
        # Clean up extra whitespace
        result = _re2.sub(r'\n\s*\n', '\n', result)

    return result.strip()


def _clean_sql(sql: str) -> str:
    """Strip LLM artifacts: -- comment lines, markdown text before/after SQL."""
    # Remove lines that start with -- (SQL comments) that are likely LLM narrative
    lines = sql.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip pure comment lines that are LLM narrative (not inline comments)
        if stripped.startswith("--") and not any(
            kw in stripped.lower() for kw in ["select", "from", "where", "join", "group", "order", "having", "limit"]
        ):
            continue
        # Skip markdown-like narrative lines
        if stripped.startswith("#") and "```" not in stripped:
            continue
        cleaned.append(line)
    result = "\n".join(cleaned).strip()
    # Remove ```sql ... ``` wrappers if present
    result = _re.sub(r"^```sql\s*\n?", "", result, flags=_re.IGNORECASE)
    result = _re.sub(r"\n?```\s*$", "", result)
    return result.strip()

# ── SQL generation prompt for hybrid search context ──

_GENERATE_SQL_PROMPT = """You are a Data Engineer expert in {dialect} SQL.

Write ONLY a **{dialect}** SQL query to answer this question: "{question}"

Important {dialect}-specific rules:
- Use {dialect}-compatible syntax only.
- Do NOT use features from other dialects (e.g., no ILIKE for SQLite, no :: casting).
- For string matching, use LIKE with % wildcards.

## SQL EXAMPLES (from graph search — for reference, NOT to copy directly)
These are similar questions with their SQL. They show possible approaches but may over-use JOINs.
**WARNING: DO NOT copy JOIN patterns from examples blindly.** If a simpler direct-column approach exists (check BUSINESS CONTEXT), use it instead.
{sql_examples}

## TABLE DOCUMENTATION (from graph schema — PRIMARY reference for schema)
These are the actual database tables with exact column names, types, keys, and dependencies. All table/column names below are authoritative.
{table_docs}

## ENTITY & VALUE HINTS (from graph search)
{entity_value_hints}

## BUSINESS CONTEXT (from documentation — SECONDARY, for reference only)
{business_docs}

## CRITICAL INSTRUCTIONS
1. **DO NOT write any text, comments, or explanations. Return ONLY the SQL.**
2. **Read the BUSINESS CONTEXT section FIRST.** It contains rules that OVERRIDE both SQL examples and table documentation. These rules tell you: when to use a direct column vs a JOIN, when to add temporal filters, and what enum values mean.
3. **THE DDL IS THE ONLY SOURCE OF TRUTH FOR COLUMNS.** Every table's DDL is listed above. You MUST read it. If a column is NOT in the DDL, it DOES NOT EXIST — do NOT use it. Example: Order_Items DDL has only order_item_id, order_id, product_id — there is NO quantity column. \"Items per order\" = COUNT(*) grouped by order_id.
4. **Apply business context rules STRICTLY.** This includes: column selection (prefer _name over _id for human-facing questions), address patterns (use direct columns), temporal filters (date_to IS NULL for "currently").
5. **Temporal filter ONLY for present tense.** \"currently\", \"now\", \"active\" → add `date_to IS NULL`. Past tense (\"have held\", \"ever\", \"previously\") → NO date filter. The date filter means \"right now\" — do not add it for historical questions.
6. **Follow the counting and aggregation rules in BUSINESS CONTEXT.** Junction/temporal tables require special handling — read the rules there.
7. **CHECK: Remove any JOIN that does not contribute a column to SELECT, WHERE, or GROUP BY.** If you JOIN a table but never use its columns, remove the JOIN.
6. Remove empty/NULL values before aggregation.
7. Format as ```sql ... ``` ONLY."""


class HybridText2SQLAgent:
    """Agent with graph-native retrieval and SQL generation.

    Pipeline:
    1. Normalize question via LLM → extract entities, keywords, enum hints
    2. Graph search: 6-way Neo4j fusion (vector + fulltext + graph)
    3. ChromaDB search: query tables_document + business_document with
       question, entities, and enum values
    4. Merge all results and rerank with cross-encoder
    5. Build table docs from Neo4j graph + ChromaDB docs
    6. Build entity/value hints from all search results
    7. Generate SQL with custom prompt using all data
    8. Optional: Reflect and retry with different model
    """

    def __init__(
        self,
        config: dict,
        neo4j_client: Neo4jClient,
        generation_model: str = "glm-4-flash",
        reflection_model: str = "glm-4-plus",
        use_reflection: bool = True,
    ):
        from t2sql.agent import get_sql_agent

        # Minimal chroma agent — only for normalize_and_structure + router
        self._chroma_agent = get_sql_agent(config.get("descriptors_folder"))
        self._router = self._chroma_agent._router
        self._dialect = self._chroma_agent._dialect
        self._schema = self._chroma_agent._schema
        self._business_rules = self._chroma_agent.business_rules_string
        self._neo4j = neo4j_client
        self._hybrid_search = HybridCypherSearch(
            neo4j_client, n_graph=15, n_vector=15, n_hybrid=15,
        )
        self._chroma_docs = HybridChromaClient()
        self._reranker = Reranker()
        self.use_reflection = use_reflection
        self.generation_model = generation_model
        self.reflection_model = reflection_model

    # ── Table documentation from Neo4j graph ──

    async def _build_table_docs_from_graph(
        self, table_names: list[str]
    ) -> str:
        """Build rich table documentation by querying Neo4j Table/Column nodes.

        Returns a markdown string with per-table sections containing:
        name, summary, purpose, dependencies, keys, and columns with descriptions.
        """
        if not table_names:
            return ""

        docs_parts = []
        for tbl in table_names:
            records = await self._neo4j.execute_read(
                """MATCH (t:Table {name: $name})
                   OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
                   OPTIONAL MATCH (t)-[:HAS_PRIMARY_KEY]->(pk:Column)
                   OPTIONAL MATCH (t)-[:HAS_FOREIGN_KEY]->(fk:Column)
                   OPTIONAL MATCH (t)-[:CONNECTED_TO]->(ct:Table)
                   RETURN t.name AS name, t.summary AS summary,
                          t.purpose AS purpose, t.dependencies AS dependencies,
                          t.keys AS keys, t.ddl AS ddl,
                          collect(DISTINCT {name: c.name, type: c.data_type,
                                            desc: c.description, is_pk: c.is_pk,
                                            is_fk: c.is_fk}) AS columns,
                          collect(DISTINCT pk.name) AS pk_cols,
                          collect(DISTINCT {col: fk.name, ref_table: fk.ref_table,
                                            ref_col: fk.ref_column}) AS fk_cols,
                          collect(DISTINCT ct.name) AS connected_tables
                   LIMIT 1""",
                name=tbl,
            )
            if not records:
                continue
            r = records[0]

            lines = [f"### Table: `{r.get('name', tbl)}`"]
            if r.get("ddl"):
                lines.append(f"**DDL:** `{r['ddl']}`")
            if r.get("summary"):
                lines.append(f"**Summary:** {r['summary']}")
            if r.get("purpose"):
                lines.append(f"**Purpose:** {r['purpose']}")
            if r.get("dependencies"):
                lines.append(f"**Dependencies:** {r['dependencies']}")
            if r.get("keys"):
                lines.append(f"**Keys:** {', '.join(r['keys'])}")
            if r.get("connected_tables"):
                lines.append(f"**Connected Tables:** {', '.join(r['connected_tables'])}")

            # Columns
            lines.append("**Columns:**")
            lines.append("| Column | Type | PK | FK | Description |")
            lines.append("|--------|------|----|----|-------------|")
            for col in (r.get("columns") or []):
                if not col.get("name"):
                    continue
                pk = "✓" if col.get("is_pk") else ""
                fk = "✓" if col.get("is_fk") else ""
                desc = (col.get("desc") or "")[:100]
                lines.append(
                    f"| `{col['name']}` | {col.get('type', '')} | {pk} | {fk} | {desc} |"
                )

            # Foreign key details
            fk_cols = r.get("fk_cols") or []
            if fk_cols and any(f.get("col") for f in fk_cols):
                lines.append("**Foreign Keys:**")
                for fk in fk_cols:
                    if fk.get("col"):
                        lines.append(
                            f"- `{fk['col']}` → `{fk.get('ref_table', '?')}."
                            f"{fk.get('ref_col', '?')}`"
                        )

            docs_parts.append("\n".join(lines))

        return "\n\n".join(docs_parts)

    # ── Entity & Value hints from hybrid search results ──

    @staticmethod
    def _build_entity_value_hints(
        hybrid_results: list[dict], chroma_docs: dict | None = None
    ) -> str:
        """Extract entity and enum-value hints from graph search + ChromaDB docs.

        Returns a markdown string summarising which entities and values
        were found, which tables/columns they map to, and relevant business
        context from ChromaDB (enum definitions, address pattern, etc.).
        """
        entities = {}
        values = {}
        biz_sections = []

        # ── From graph results ──
        for item in (hybrid_results or []):
            src = item.get("source", "")

            if src == "fulltext_entity":
                ent = item.get("entity", "")
                tbl = item.get("table", "")
                cols = item.get("columns", [])
                if ent and tbl:
                    key = f"{tbl}.{ent}"
                    if key not in entities:
                        entities[key] = {"entity": ent, "table": tbl, "columns": cols}

            if src == "fulltext_value":
                val = item.get("value", "")
                meaning = item.get("meaning", "")
                tbl = item.get("table", "")
                col = item.get("column", "")
                if val and tbl:
                    key = f"{tbl}.{col}"
                    if key not in values:
                        values[key] = {
                            "value": val, "meaning": meaning, "table": tbl, "column": col,
                        }

        # ── From ChromaDB business docs ──
        if chroma_docs:
            for bd in chroma_docs.get("business_docs", [])[:3]:
                name = bd.get("metadata", {}).get("name", "")
                doc = bd.get("document", "")
                if not doc:
                    continue
                # For enum_values and address_pattern docs, extract key info
                if name == "enum_values":
                    # Extract the markdown content directly
                    biz_sections.append(doc)
                elif name == "address_pattern":
                    # Extract the key design insight about customer_address vs addresses table
                    lines = doc.split("\n")
                    summary_lines = []
                    for line in lines:
                        if any(kw in line.lower() for kw in [
                            "use `customer_address`", "use `addresses`",
                            "when to use each", "immediate access",
                            "dual address", "trade-off",
                        ]):
                            summary_lines.append(line)
                    if summary_lines:
                        biz_sections.append(
                            "### Address Design Pattern (from business docs)\n"
                            + "\n".join(summary_lines[:10])
                        )

        if not entities and not values and not biz_sections:
            return ""

        lines = []
        if entities:
            lines.append("### Entity Hints")
            lines.append("These entities were found in the graph and map to specific tables/columns:")
            for key, e in entities.items():
                cols_str = ", ".join(f"`{c}`" for c in e["columns"][:5])
                lines.append(f"- **{e['entity']}** → table `{e['table']}`, columns: {cols_str or '(none)'}")

        if values:
            lines.append("### Value Constraints (Enum Values)")
            lines.append("These are known values for specific columns:")
            for key, v in values.items():
                lines.append(
                    f"- `{v['table']}.{v['column']}` = **\"{v['value']}\"** "
                    f"({v['meaning'][:80]})"
                )

        if biz_sections:
            lines.append("### Business Context (from documentation)")
            for section in biz_sections:
                # Truncate very long sections
                if len(section) > 2000:
                    section = section[:2000] + "\n..."
                lines.append(section)

        return "\n".join(lines)

    # ── SQL Generation ──

    async def _generate_sql(
        self,
        question: str,
        sql_examples: str,
        table_docs: str,
        entity_value_hints: str,
        dialect: str = "sqlite",
        business_docs: str = "",
    ) -> str:
        """Generate SQL using all hybrid search context."""
        prompt = _GENERATE_SQL_PROMPT.format(
            dialect=dialect,
            business_docs=business_docs or "(no specific business context available)",
            question=question,
            sql_examples=sql_examples or "(none)",
            table_docs=table_docs or "(none)",
            entity_value_hints=entity_value_hints or "(none)",
        )

        messages = [
            {"role": "user", "content": prompt},
        ]

        # Generate multiple candidates, then majority vote
        sqls = []
        n_candidates = 5
        try:
            ai_msg = await self._router.acompletion(
                model=self.generation_model,
                messages=messages,
                n=n_candidates,
                temperature=0.3,
            )
        except Exception:
            # Fallback: some models don't support n>1
            ai_msg = await self._router.acompletion(
                model=self.generation_model,
                messages=messages,
            )

        for ch in ai_msg.choices:
            try:
                code = parse_code(ch.message.content)
                if code and code.strip():
                    sqls.append(code.strip())
            except Exception:
                pass

        if not sqls:
            return "SELECT 1"

        # Majority vote
        counter = Counter(sqls)
        return counter.most_common(1)[0][0]

    # ── Main entry point ──

    async def make_sql(
        self,
        question: str,
        use_reflection: bool | None = None,
        dialect: str = "sqlite",
        **kwargs,
    ) -> tuple[str, str]:
        """Generate SQL using hybrid graph-native search.

        Args:
            question: Natural language question.
            use_reflection: Override instance default for reflection.
            dialect: SQL dialect to generate (sqlite, postgresql, mysql, etc.).

        Returns:
            (sql, step_description) tuple.
        """
        do_reflection = self.use_reflection if use_reflection is None else use_reflection

        # 1. Normalize + embed IN PARALLEL (independent)
        norm_task = self._chroma_agent.normalize_and_structure(question)
        embed_task = self._neo4j.generate_embedding(question)
        structure, embedding = await asyncio.gather(norm_task, embed_task)

        # 2. Graph search + ALL ChromaDB queries IN PARALLEL
        chroma_query = f"{question} {structure.requested_entities or ''}".strip()
        entity_queries = []
        if structure.requested_entities:
            entity_queries.append(structure.requested_entities)
        if structure.main_clause:
            entity_queries.append(structure.main_clause)

        graph_task = self._hybrid_search.hybrid_search(
            question=question,
            entities=[structure.requested_entities] if structure.requested_entities else [],
            keywords=[structure.main_clause] if structure.main_clause else [],
            embedding=embedding,
        )
        chroma_tasks = [self._chroma_docs.query_async(chroma_query, 5)]
        for eq in entity_queries:
            chroma_tasks.append(self._chroma_docs.query_async(eq, 3))

        all_search_results = await asyncio.gather(graph_task, *chroma_tasks)
        graph_results = all_search_results[0]
        chroma_results_list = all_search_results[1:]

        chroma_docs = {"tables_docs": [], "business_docs": []}
        for cr in chroma_results_list:
            chroma_docs["tables_docs"].extend(cr["tables_docs"])
            chroma_docs["business_docs"].extend(cr["business_docs"])

        # 3. Rerank graph results
        graph_reranked = list(graph_results or [])
        if graph_reranked:
            graph_reranked = self._reranker.rerank(question, graph_reranked)

        # 4. Extract SQL examples + collect tables
        # Filter examples that contradict business docs
        biz_doc_names = {bd.get("metadata", {}).get("name", "")
                         for bd in chroma_docs.get("business_docs", [])}

        sql_examples_parts = []
        tables_set = set()
        for rr in (graph_reranked or []):
            q = rr.get("question", "").strip()
            s = rr.get("sql", "").strip()
            if not q or not s:
                continue
            s_upper = s.upper()

            # Business doc signal: address_pattern → skip examples that over-join
            if "address_pattern" in biz_doc_names:
                has_addr_join = any(kw in s_upper for kw in (
                    "JOIN CUSTOMER_ADDRESSES", "JOIN ADDRESSES", "JOIN SUPPLIER_ADDRESSES"))
                has_time = any(kw in q.lower() for kw in
                    ("history", "previously", "formerly", "over time", "changed"))
                if has_addr_join and not has_time:
                    continue

            # Business doc signal: temporal_rules → skip date-filter examples
            # when question has no PRESENT-TENSE temporal keyword
            if "temporal_rules" in biz_doc_names:
                has_temporal = any(kw in q.lower() for kw in
                    ("currently", "current", "now", "at present", "active"))
                # Past-tense keywords are NOT temporal triggers
                has_past = any(kw in q.lower() for kw in
                    ("have held", "ever", "previously", "formerly", "used to", "in the past"))
                if not has_temporal and not has_past and "DATE_TO IS NULL" in s_upper:
                    continue
                # If past tense and example has date filter, keep it
                # (it's still a useful structural example even with the filter)

            # Business doc signal: temporal_rules → when question asks "which/who/name",
            # prefer examples that return name columns, not bare IDs
            asks_who = any(kw in q.lower() for kw in
                ("which staff", "who", "names of", "name of"))
            asks_id_explicitly = "id and name" in q.lower() or "ids and names" in q.lower()
            if asks_who and not asks_id_explicitly:
                s_no_select = s_upper.split("FROM")[0].replace("SELECT", "")
                has_name = any(n in s_no_select for n in ("_NAME", "STAFF_NAME"))
                is_bare_id = not has_name and any(
                    id_col in s_no_select for id_col in
                    ("STAFF_ID", "CUSTOMER_ID", "PRODUCT_ID", "SUPPLIER_ID"))
                if is_bare_id:
                    continue  # Skip — this example returns bare IDs not human-readable names

            sql_examples_parts.append(f"QUESTION: {q}\nSQL: {s}")
            for tbl in (rr.get("tables") or []):
                tables_set.add(tbl)
            tbl = rr.get("table", "")
            if tbl:
                tables_set.add(tbl)

        sql_examples = "\n\n".join(sql_examples_parts[:8])

        # 5. Build table documentation from Neo4j graph
        table_list = list(tables_set)
        graph_docs = await self._build_table_docs_from_graph(table_list)

        # Augment with ChromaDB table docs (relevant business context)
        chroma_table_docs = ""
        seen_tables = set()
        for td in chroma_docs.get("tables_docs", [])[:5]:
            tbl = td.get("metadata", {}).get("table", "")
            if tbl and tbl not in seen_tables:
                seen_tables.add(tbl)
                chroma_table_docs += f"\n\n{td['document']}"
        if chroma_table_docs:
            table_docs = chroma_table_docs + "\n\n" + graph_docs
        else:
            table_docs = graph_docs

        # Augment with ChromaDB business docs
        chroma_biz_docs = ""
        for bd in chroma_docs.get("business_docs", [])[:3]:
            chroma_biz_docs += f"\n\n{bd['document']}"

        # 6. Build entity/value hints from graph + ChromaDB results
        entity_value_hints = self._build_entity_value_hints(graph_reranked or [], chroma_docs)

        # 6. Generate SQL
        pred_sql = await self._generate_sql(
            question=question,
            sql_examples=sql_examples,
            table_docs=table_docs,
            entity_value_hints=entity_value_hints,
            dialect=dialect,
            business_docs=chroma_biz_docs,
        )
        # Clean LLM artifacts: strip -- comments and markdown before SQL
        pred_sql = _clean_sql(pred_sql)
        pred_sql = pred_sql.replace("public.", "")
        # Post-process: for human-facing questions, prefer name columns over bare IDs
        pred_sql = _prefer_human_readable_columns(pred_sql, question)

        # 10. Reflection (optional)
        step = "HYBRID_GRAPH_CHROMA_SEARCH"
        if do_reflection:
            try:
                reflection_node = ReflectionNode(
                    router=self._router,
                    generation_model=self.generation_model,
                    reflection_model=self.reflection_model,
                )
                spider_dir = os.path.join(os.path.dirname(__file__), "..", "spider")
                async def _regenerate(feedback_q: str) -> str:
                    sql, _ = await self.make_sql(feedback_q, use_reflection=False, dialect=dialect)
                    return sql

                pred_sql, reflection = await reflection_node.reflect_and_retry(
                    pred_sql, question, _regenerate, spider_dir, max_retries=1
                )
                if reflection.get("passed"):
                    step = "HYBRID_GRAPH_CHROMA_SEARCH_WITH_REFLECTION"
            except Exception as e:
                logger.warning(f"Reflection failed: {e}")

        return pred_sql, step

    async def close(self) -> None:
        await self._neo4j.close()
