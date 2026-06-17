"""Graph data ingestion pipeline.

Parses SQL schema into Table/Column graph nodes and ingests question-SQL
training pairs with LLM-aided entity, keyword, and SQL-template extraction.
"""

import asyncio
import json
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hybridsearch.neo4j.client import Neo4jClient
from hybridsearch.neo4j.schema import (
    NODE_TABLE,
    NODE_COLUMN,
    NODE_ENTITY,
    NODE_QUESTION,
    NODE_SQL,
    NODE_SQL_TEMPLATE,
    NODE_KEYWORD,
    NODE_VALUE,
    REL_HAS_COLUMN,
    REL_HAS_PRIMARY_KEY,
    REL_HAS_FOREIGN_KEY,
    REL_CONNECTED_TO,
    REL_HAS_VALUE,
    REL_MAPS_TO,
    REL_USES_TABLE,
    REL_USES_COLUMN,
    REL_HAS_ENTITY,
    REL_HAS_SQL,
    REL_INSTANTIATES,
    REL_HAS_KEYWORD,
    REL_SYNONYM_OF,
)

# ── Regexes for table/column extraction from raw SQL ──

_TABLE_RE = re.compile(
    r'(?:\bFROM\b|\bJOIN\b|\bINNER\s+JOIN\b|\bLEFT\s+(?:OUTER\s+)?JOIN\b'
    r'|\bRIGHT\s+(?:OUTER\s+)?JOIN\b|\bFULL\s+(?:OUTER\s+)?JOIN\b'
    r'|\bCROSS\s+JOIN\b)\s+(\w+)',
    re.IGNORECASE,
)
_SELECT_COL_RE = re.compile(r'\bSELECT\s+(?:DISTINCT\s+)?(.+?)\s*\bFROM\b', re.IGNORECASE | re.DOTALL)
_WHERE_COL_RE = re.compile(r'\bWHERE\b\s+(.+?)(?:\bGROUP\b|\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', re.IGNORECASE | re.DOTALL)
_GROUP_COL_RE = re.compile(r'\bGROUP\s+BY\b\s+(.+?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', re.IGNORECASE | re.DOTALL)
_COL_NAME_RE = re.compile(r'\b(\w+\.)?(\w+)\b')


def _extract_table_names_from_sql(sql: str) -> list[str]:
    """Parse table names from SQL FROM/JOIN clauses."""
    tables = _TABLE_RE.findall(sql)
    return list(dict.fromkeys(t.lower() for t in tables))


def _extract_column_names_from_sql(sql: str) -> list[str]:
    """Extract a rough list of column names referenced in SELECT/WHERE/GROUP BY.

    This is a heuristic — not a full parser. Names matching SQL keywords are kept
    because the graph stores them for relationship traversal, not for execution.
    """
    columns = set()
    for pattern, group in [(_SELECT_COL_RE, 1), (_WHERE_COL_RE, 1), (_GROUP_COL_RE, 1)]:
        m = pattern.search(sql)
        if m:
            text = m.group(group)
            for cm in _COL_NAME_RE.finditer(text):
                col = cm.group(2).lower()
                if not col.isdigit():
                    columns.add(col)
    return list(columns)


# ── LLM extraction prompts ──

_EXTRACT_ENTITIES_PROMPT = """Extract business entities from this question.
An entity is a real-world concept referenced in the question (e.g., "staff assignment", "customer address", "product order", "department").
Do NOT include generic terms like "name" or "id" as standalone entities.

Question: {question}
SQL: {sql}
Tables used: {tables}

Return JSON: {{"entities": [{{"name": str, "maps_to_table": str | null, "maps_to_column": str | null}}]}}
Only output valid JSON, no markdown or extra text."""

_EXTRACT_KEYWORDS_PROMPT = """Extract important domain keywords from this question and generate synonyms.
Keywords should be the most important content words that capture the question's intent.

Question: {question}

Return JSON: {{"keywords": [{{"word": str, "synonyms": [str]}}]}}
Only output valid JSON, no markdown or extra text."""

_GENERATE_SQL_TEMPLATE_PROMPT = """Convert this SQL query to a parameterized template by replacing literal values
(strings, numbers, dates) with descriptive placeholders like {{value}}, {{name}}, {{date}}, {{threshold}}, etc.

Original SQL:
{sql}

Return JSON: {{"template": str}}
Only output valid JSON, no markdown or extra text."""


class GraphIngestor:
    """Ingests database schema and training data into the Neo4j graph."""

    def __init__(self, client: Neo4jClient, router=None, default_model: str = "glm-4-flash"):
        self.client = client
        self._router = router
        self.default_model = default_model

    async def _llm_json(self, prompt: str) -> dict:
        """Call the LLM via router and parse JSON response."""
        from t2sql.utils import parse_json

        if self._router is None:
            raise RuntimeError("No LLM router configured for GraphIngestor")

        response = await self._router.acompletion(
            model=self.default_model,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        return parse_json(content)

    # ── Schema ingestion ──

    async def ingest_schema(self, schema_path: str, db_name: str = "department_store") -> None:
        """Parse schema.sql and create Table + Column nodes with PK/FK relationships.

        Args:
            schema_path: Path to the SQL schema file.
            db_name: Database name for metadata.
        """
        from t2sql.utils import parse_schema_sql

        df = parse_schema_sql(schema_path, db_name=db_name)

        # Create Table nodes
        table_names = df["table_name"].unique()
        for tbl in table_names:
            await self.client.execute_write(
                f"MERGE (t:{NODE_TABLE} {{name: $name}}) SET t.db_id = $db_id",
                name=tbl.lower(),
                db_id=db_name,
            )

        # Create Column nodes and relationships
        for _, row in df.iterrows():
            tbl = row["table_name"].lower()
            col = row["column_name"].lower()
            data_type = row.get("data_type", "VARCHAR")
            is_pk = bool(row.get("is_pk", False))
            fk_table = row.get("fk_table")
            fk_column = row.get("fk_column")

            # Column node under the table namespace (name + table as unique key)
            col_key = f"{tbl}.{col}"
            await self.client.execute_write(
                f"""MERGE (c:{NODE_COLUMN} {{name: $name, table: $table}})
                    SET c.data_type = $data_type,
                        c.is_pk = $is_pk,
                        c.is_fk = $is_fk,
                        c.col_key = $col_key""",
                name=col,
                table=tbl,
                data_type=str(data_type),
                is_pk=is_pk,
                is_fk=fk_table is not None and len(str(fk_table)) > 0,
                col_key=col_key,
            )

            # HAS_COLUMN
            await self.client.execute_write(
                f"""MATCH (t:{NODE_TABLE} {{name: $table}})
                    MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                    MERGE (t)-[:{REL_HAS_COLUMN}]->(c)""",
                table=tbl,
                col_key=col_key,
            )

            # HAS_PRIMARY_KEY
            if is_pk:
                await self.client.execute_write(
                    f"""MATCH (t:{NODE_TABLE} {{name: $table}})
                        MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                        MERGE (t)-[:{REL_HAS_PRIMARY_KEY}]->(c)""",
                    table=tbl,
                    col_key=col_key,
                )

            # HAS_FOREIGN_KEY
            fk_val = str(fk_table) if fk_table else ""
            if fk_val and fk_val.lower() not in ("none", "nan", ""):
                ref_table = fk_val.lower()
                ref_col = (str(fk_column).lower()) if fk_column else col
                await self.client.execute_write(
                    f"""MATCH (t:{NODE_TABLE} {{name: $table}})
                        MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                        MERGE (t)-[:{REL_HAS_FOREIGN_KEY} {{
                            ref_table: $ref_table, ref_column: $ref_col
                        }}]->(c)""",
                    table=tbl,
                    col_key=col_key,
                    ref_table=ref_table,
                    ref_col=ref_col,
                )

        print(f"Ingested schema: {len(table_names)} tables, {len(df)} columns")

    # ── Schema semantic inference (LLM-powered) ──

    _SCHEMA_TO_MD_PROMPT = """You are a Data Engineer. Based on the DDL below, write a detailed,
    human-readable description for the table `{table_name}`.

    DDL:
    {ddl}

    Include:
    1. **Purpose** — what business concept this table represents
    2. **Columns** — describe each column's meaning and data type
    3. **Relationships** — how this table connects to others (foreign keys, join logic)
    4. **Usage notes** — when would you query this table?

    Be specific. Do NOT hallucinate columns that are not in the DDL."""

    _PROCESS_DOCUMENT_PROMPT = """Here is a document describing a database table:
    #####
    {document}
    #####
    Make the TABLE DESCRIPTION more readable. Keep all fields, just remove empty ones.
    Return as JSON:
    {{"name": str <name of table>,
     "summary": str <short summary about table>,
     "purpose": str <purpose of the table>,
     "dependencies_thoughts": str <relations with other tables. Specify keys, ids, MANDATORY specify <Table name> and connected key>,
     "keys": List[str] <columns that are keys used for connecting to other tables>,
     "connected_tables": List[str] <names of tables connected with this one. Do NOT hallucinate>,
     "columns": [{{"column": str, "description": str}}] <list of columns with descriptions>
    }}
    DO NOT violate JSON structure!"""

    _EXTRACT_ENTITIES_PROMPT = """Here is a table description:
    #####
    {table_info}
    #####
    Extract business entities that can be inferred from this table based on its purpose.
    An entity is a real-world concept (2-3 words, e.g., "staff assignment", "customer order").
    Return as JSON:
    {{"entities": [{{"name": str, "maps_to_table": str, "maps_to_column": str | null}}]}}
    DO NOT violate JSON structure! Do NOT repeat the table name itself as an entity."""

    async def _infer_schema_semantics(
        self, schema_path: str, db_name: str = "department_store"
    ) -> None:
        """LLM-powered semantic inference for tables, columns, and relationships.

        For each table:
        1. Read the DDL subset relevant to that table
        2. LLM generates a rich Markdown description
        3. LLM processes the MD into structured JSON (summary, purpose, keys,
           dependencies, connected_tables, column descriptions)
        4. Update Table nodes with descriptions, Column nodes with descriptions
        5. Create CONNECTED_TO relationships between tables
        6. Extract entities and create Entity nodes with MAPS_TO from columns

        This mirrors the existing t2sql pipeline:
        PREPARE_MD_FROM_SCHEMA → PROCESS_DOCUMENT → store
        """
        from t2sql.utils import parse_json

        print("Inferring schema semantics via LLM...")

        # Read full DDL
        with open(schema_path) as f:
            full_ddl = f.read()

        # Get table names already in the graph
        records = await self.client.execute_read(
            f"MATCH (t:{NODE_TABLE}) RETURN t.name AS name ORDER BY name"
        )
        table_names = [r["name"] for r in records]

        for tbl in table_names:
            print(f"  Inferring semantics for: {tbl}")

            # Extract just this table's CREATE TABLE statement from DDL
            tbl_ddl = self._extract_table_ddl(full_ddl, tbl)

            try:
                # Step 1: LLM generates rich MD description from DDL
                md_prompt = self._SCHEMA_TO_MD_PROMPT.format(
                    table_name=tbl, ddl=tbl_ddl or full_ddl
                )
                md_response = await self._router.acompletion(
                    model=self.default_model,
                    messages=[{"role": "user", "content": md_prompt}],
                )
                md_description = md_response.choices[0].message.content

                # Step 2: LLM processes the MD into structured JSON (ProcessDocumentLLM-like)
                proc_prompt = self._PROCESS_DOCUMENT_PROMPT.format(document=md_description)
                proc_response = await self._router.acompletion(
                    model=self.default_model,
                    messages=[{"role": "user", "content": proc_prompt}],
                )
                doc = parse_json(proc_response.choices[0].message.content)

                summary = doc.get("summary", "")
                purpose = doc.get("purpose", "")
                dependencies = doc.get("dependencies_thoughts", "")
                keys = doc.get("keys", [])
                connected_tables = doc.get("connected_tables", [])
                columns_info = doc.get("columns", [])

                # Step 3: Update Table node with descriptions
                await self.client.execute_write(
                    f"""MATCH (t:{NODE_TABLE} {{name: $name}})
                        SET t.summary = $summary,
                            t.purpose = $purpose,
                            t.dependencies = $dependencies,
                            t.keys = $keys,
                            t.db_id = $db_id""",
                    name=tbl,
                    summary=summary,
                    purpose=purpose,
                    dependencies=dependencies,
                    keys=keys,
                    db_id=db_name,
                )

                # Step 4: Update Column nodes with descriptions
                for col_info in columns_info:
                    col_name = col_info.get("column", "").lower()
                    col_desc = col_info.get("description", "")
                    if col_name:
                        col_key = f"{tbl}.{col_name}"
                        await self.client.execute_write(
                            f"""MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                                SET c.description = $description""",
                            col_key=col_key,
                            description=col_desc,
                        )

                # Step 5: CONNECTED_TO relationships between tables
                for connected_tbl in connected_tables:
                    ct = connected_tbl.lower().strip()
                    if ct and ct != tbl:
                        await self.client.execute_write(
                            f"""MATCH (t1:{NODE_TABLE} {{name: $t1}})
                                MATCH (t2:{NODE_TABLE} {{name: $t2}})
                                MERGE (t1)-[:{REL_CONNECTED_TO} {{
                                    keys: $keys
                                }}]->(t2)""",
                            t1=tbl,
                            t2=ct,
                            keys=keys,
                        )

                # Step 6: Extract entities and create Entity nodes with MAPS_TO
                table_info = f"Table: {tbl}\nSummary: {summary}\nPurpose: {purpose}"
                ent_prompt = self._EXTRACT_ENTITIES_PROMPT.format(table_info=table_info)
                ent_response = await self._router.acompletion(
                    model=self.default_model,
                    messages=[{"role": "user", "content": ent_prompt}],
                )
                ent_result = parse_json(ent_response.choices[0].message.content)
                entities = ent_result.get("entities", [])

                for ent in entities:
                    ent_name = ent.get("name", "").strip()
                    if not ent_name:
                        continue
                    maps_to_col = (ent.get("maps_to_column") or "").lower()
                    maps_to_table = (ent.get("maps_to_table") or tbl).lower()
                    await self.client.execute_write(
                        f"MERGE (e:{NODE_ENTITY} {{name: $name}}) SET e.source_table = $table",
                        name=ent_name, table=maps_to_table,
                    )
                    # Column MAPS_TO Entity
                    if maps_to_col and maps_to_table:
                        col_key = f"{maps_to_table}.{maps_to_col}"
                        await self.client.execute_write(
                            f"""MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                                MATCH (e:{NODE_ENTITY} {{name: $name}})
                                MERGE (c)-[:{REL_MAPS_TO}]->(e)""",
                            col_key=col_key, name=ent_name,
                        )

                print(f"    summary={summary[:60]}..., "
                      f"connected_tables={connected_tables}, "
                      f"entities={len(entities)}, "
                      f"columns_described={len(columns_info)}")

            except Exception as e:
                print(f"    WARNING: semantic inference failed for {tbl}: {e}")

        print(f"Semantic inference complete for {len(table_names)} tables.")

    @staticmethod
    def _extract_table_ddl(full_ddl: str, table_name: str) -> str | None:
        """Extract the CREATE TABLE statement for a specific table from DDL."""
        import re
        pattern = re.compile(
            rf'CREATE\s+TABLE\s+.*?{re.escape(table_name)}.*?\)\s*;',
            re.IGNORECASE | re.DOTALL,
        )
        m = pattern.search(full_ddl)
        return m.group(0) if m else None

    # ── Enum value discovery ──

    _INFER_VALUE_SEMANTICS_PROMPT = """You are a Data Analyst. Given a column and its distinct
    values, infer the semantic meaning of each value.

    Table: {table_name}
    Column: {column_name}
    Column description: {col_description}
    Distinct values: {values}

    For each value, provide:
    - value: the raw value
    - meaning: human-readable semantic meaning (e.g., "PartFilled" → "Partially Filled")
    - synonyms: list of search keywords people might use for this value

    Return JSON:
    {{"values": [{{"value": str, "meaning": str, "synonyms": [str]}}]}}
    Only output valid JSON."""

    _CODE_COLUMN_PATTERNS = ('_code', '_status', '_type', 'method', 'gender')

    async def _ingest_enum_values(self, spider_dir: str, db_name: str = "department_store") -> None:
        """Discover enum-like columns, infer value semantics via LLM, create Value nodes.

        Queries the SQLite database for distinct values of code/status/type columns,
        uses LLM to infer human-readable meaning, and creates Value nodes linked
        to their parent Column nodes.
        """
        import sqlite3
        from t2sql.utils import parse_json

        db_path = os.path.join(spider_dir, f"{db_name}.sqlite")
        if not os.path.exists(db_path):
            print(f"No SQLite DB at {db_path}, skipping enum discovery.")
            return

        print("Discovering enum values from SQLite...")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Find code/status/type columns
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]

        code_cols = {}
        for tbl in tables:
            cur.execute(f'PRAGMA table_info("{tbl}")')
            for row in cur.fetchall():
                col = row[1].lower()
                if any(kw in col for kw in self._CODE_COLUMN_PATTERNS):
                    key = f"{tbl}.{row[1]}"
                    try:
                        cur.execute(
                            f'SELECT DISTINCT "{row[1]}" FROM "{tbl}" '
                            f'WHERE "{row[1]}" IS NOT NULL ORDER BY 1'
                        )
                        vals = [r[0] for r in cur.fetchall()]
                        # Only include columns with reasonable enum cardinality (2-15 values)
                        if 1 < len(vals) <= 15 and not all(v.isdigit() for v in vals if v):
                            code_cols[key] = vals
                    except Exception:
                        pass
        conn.close()

        print(f"Found {len(code_cols)} enum-like columns: {list(code_cols.keys())}")

        for full_name, values in code_cols.items():
            tbl_name, col_name = full_name.split(".", 1)
            table_lower = tbl_name.lower()
            col_lower = col_name.lower()
            col_key = f"{table_lower}.{col_lower}"

            # Get column description from graph
            records = await self.client.execute_read(
                f"""MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                    OPTIONAL MATCH (t:{NODE_TABLE} {{name: $table}})
                    RETURN c.description AS col_desc, t.summary AS table_summary""",
                col_key=col_key, table=table_lower,
            )
            col_desc = (records[0].get("col_desc") or col_name) if records else col_name

            try:
                prompt = self._INFER_VALUE_SEMANTICS_PROMPT.format(
                    table_name=tbl_name, column_name=col_name,
                    col_description=col_desc, values=values,
                )
                response = await self._router.acompletion(
                    model=self.default_model,
                    messages=[{"role": "user", "content": prompt}],
                )
                result = parse_json(response.choices[0].message.content)
                inferred = result.get("values", [])
            except Exception as e:
                print(f"  LLM inference failed for {full_name}: {e}")
                # Fallback: create basic values without semantics
                inferred = [{"value": v, "meaning": v, "synonyms": []} for v in values]

            for item in inferred:
                raw = str(item.get("value", "")).strip()
                if not raw:
                    continue
                meaning = item.get("meaning", raw)
                synonyms = item.get("synonyms", [])

                # Create Value node
                await self.client.execute_write(
                    f"""MERGE (v:{NODE_VALUE} {{value: $value, column: $col_key}})
                        SET v.meaning = $meaning,
                            v.synonyms = $synonyms,
                            v.table = $table,
                            v.column_name = $column""",
                    value=raw, col_key=col_key, meaning=meaning,
                    synonyms=synonyms, table=table_lower, column=col_name,
                )

                # Link Column -[:HAS_VALUE]-> Value
                await self.client.execute_write(
                    f"""MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                        MATCH (v:{NODE_VALUE} {{value: $value, column: $col_key}})
                        MERGE (c)-[:{REL_HAS_VALUE}]->(v)""",
                    col_key=col_key, value=raw,
                )

            print(f"  {full_name}: {len(inferred)} values")

        # Count
        cnt = await self.client.execute_read(
            f"MATCH (v:{NODE_VALUE}) RETURN count(v) AS cnt"
        )
        print(f"Enum value ingestion complete. {cnt[0]['cnt']} Value nodes created.")

    # ── Train data ingestion ──

    async def ingest_train_data(self, train_data: list[dict]) -> None:
        """Ingest training question-SQL pairs into the graph.

        For each pair we:
        1. Extract tables & columns from the raw SQL
        2. Use LLM to extract entities, keywords, and SQL template
        3. Create Question, SQL, SQLTemplate, Entity, Keyword nodes
        4. Create all relationships

        Args:
            train_data: List of dicts with 'question', 'query', 'sql', 'db_id'.
        """
        total = len(train_data)
        for idx, item in enumerate(train_data):
            question = item["question"]
            query = item.get("query", "")
            db_id = item.get("db_id", "")

            # 1. Extract tables/columns from SQL
            tables = _extract_table_names_from_sql(query)
            columns = _extract_column_names_from_sql(query)

            # 2. LLM extractions (run in parallel)
            try:
                entities_prompt = _EXTRACT_ENTITIES_PROMPT.format(
                    question=question, sql=query, tables=tables
                )
                keywords_prompt = _EXTRACT_KEYWORDS_PROMPT.format(question=question)
                template_prompt = _GENERATE_SQL_TEMPLATE_PROMPT.format(sql=query)

                entities_result, keywords_result, template_result = await asyncio.gather(
                    self._llm_json(entities_prompt),
                    self._llm_json(keywords_prompt),
                    self._llm_json(template_prompt),
                )
            except Exception as e:
                print(f"  LLM extraction failed for Q{idx+1}: {e}")
                entities_result = {"entities": []}
                keywords_result = {"keywords": []}
                template_result = {"template": query}

            entities = entities_result.get("entities", [])
            keywords = (keywords_result or {}).get("keywords", [])
            sql_template = (template_result or {}).get("template", query)

            # 3. Generate embedding for the question
            try:
                embedding = await self.client.generate_embedding(question)
            except Exception:
                embedding = []

            # 4. Create graph nodes and relationships
            # Question node
            await self.client.execute_write(
                f"""MERGE (q:{NODE_QUESTION} {{text: $question}})
                    SET q.db_id = $db_id,
                        q.embedding = $embedding""",
                question=question,
                db_id=db_id,
                embedding=embedding,
            )

            # SQL node
            await self.client.execute_write(
                f"""MERGE (s:{NODE_SQL} {{text: $sql}})
                    SET s.db_id = $db_id""",
                sql=query,
                db_id=db_id,
            )

            # Question -[:HAS_SQL]-> SQL
            await self.client.execute_write(
                f"""MATCH (q:{NODE_QUESTION} {{text: $question}})
                    MATCH (s:{NODE_SQL} {{text: $sql}})
                    MERGE (q)-[:{REL_HAS_SQL}]->(s)""",
                question=question,
                sql=query,
            )

            # SQLTemplate node + INSTANTIATES
            if sql_template:
                await self.client.execute_write(
                    f"""MERGE (st:{NODE_SQL_TEMPLATE} {{template: $template}})
                        WITH st
                        MATCH (s:{NODE_SQL} {{text: $sql}})
                        MERGE (s)-[:{REL_INSTANTIATES}]->(st)""",
                    template=sql_template,
                    sql=query,
                )

            # USES_TABLE relationships
            for tbl in tables:
                await self.client.execute_write(
                    f"""MATCH (q:{NODE_QUESTION} {{text: $question}})
                        MATCH (t:{NODE_TABLE} {{name: $table}})
                        MERGE (q)-[:{REL_USES_TABLE}]->(t)""",
                    question=question,
                    table=tbl,
                )

            # USES_COLUMN relationships
            for col in columns:
                # Try to match column in any of the used tables
                for tbl in tables:
                    col_key = f"{tbl}.{col}"
                    await self.client.execute_write(
                        f"""MATCH (q:{NODE_QUESTION} {{text: $question}})
                            MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                            MERGE (q)-[:{REL_USES_COLUMN}]->(c)""",
                        question=question,
                        col_key=col_key,
                    )

            # Entity nodes + HAS_ENTITY + MAPS_TO
            for ent in entities:
                ent_name = ent.get("name", "").strip()
                if not ent_name:
                    continue
                maps_to_table = (ent.get("maps_to_table") or "").lower()
                maps_to_column = (ent.get("maps_to_column") or "").lower()

                await self.client.execute_write(
                    f"MERGE (e:{NODE_ENTITY} {{name: $name}})",
                    name=ent_name,
                )
                await self.client.execute_write(
                    f"""MATCH (q:{NODE_QUESTION} {{text: $question}})
                        MATCH (e:{NODE_ENTITY} {{name: $name}})
                        MERGE (q)-[:{REL_HAS_ENTITY}]->(e)""",
                    question=question,
                    name=ent_name,
                )
                # MAPS_TO relationship
                if maps_to_table and maps_to_column:
                    col_key = f"{maps_to_table}.{maps_to_column}"
                    await self.client.execute_write(
                        f"""MATCH (c:{NODE_COLUMN} {{col_key: $col_key}})
                            MATCH (e:{NODE_ENTITY} {{name: $name}})
                            MERGE (c)-[:{REL_MAPS_TO}]->(e)""",
                        col_key=col_key,
                        name=ent_name,
                    )

            # Keyword nodes + HAS_KEYWORD + SYNONYM_OF
            for kw in keywords:
                word = kw.get("word", "").strip().lower()
                if not word:
                    continue
                synonyms = kw.get("synonyms", [])
                await self.client.execute_write(
                    f"MERGE (k:{NODE_KEYWORD} {{word: $word}})",
                    word=word,
                )
                await self.client.execute_write(
                    f"""MATCH (q:{NODE_QUESTION} {{text: $question}})
                        MATCH (k:{NODE_KEYWORD} {{word: $word}})
                        MERGE (q)-[:{REL_HAS_KEYWORD}]->(k)""",
                    question=question,
                    word=word,
                )
                for syn in synonyms:
                    syn_word = syn.strip().lower()
                    if syn_word and syn_word != word:
                        await self.client.execute_write(
                            f"MERGE (ks:{NODE_KEYWORD} {{word: $word}})",
                            word=syn_word,
                        )
                        await self.client.execute_write(
                            f"""MATCH (k:{NODE_KEYWORD} {{word: $word}})
                                MATCH (ks:{NODE_KEYWORD} {{word: $syn}})
                                MERGE (k)-[:{REL_SYNONYM_OF}]->(ks)""",
                            word=word,
                            syn=syn_word,
                        )

            print(f"  [{idx+1}/{total}] {question[:60]}... "
                  f"tables={tables}, entities={len(entities)}, keywords={len(keywords)}")

    # ── Orchestrator ──

    async def ingest_all(
        self,
        schema_path: str,
        db_name: str = "department_store",
        train_data: list[dict] | None = None,
        clear_first: bool = True,
    ) -> None:
        """Run full ingestion: schema + training data.

        Args:
            schema_path: Path to schema.sql.
            db_name: Database name.
            train_data: List of training question-query dicts.
            clear_first: If True, clear all existing data before ingestion.
        """
        if clear_first:
            await self.client.clear_all()
            await self.client.initialize_schema()
            print("Cleared existing graph data.")

        print(f"Ingesting schema from {schema_path}...")
        await self.ingest_schema(schema_path, db_name)

        print("Inferring table/column descriptions, dependencies, and entities via LLM...")
        await self._infer_schema_semantics(schema_path, db_name)

        print("Discovering enum values and inferring semantics...")
        spider_dir = os.path.dirname(schema_path)
        await self._ingest_enum_values(spider_dir, db_name)

        if train_data:
            print(f"Ingesting {len(train_data)} training examples...")
            await self.ingest_train_data(train_data)

        print("Ingestion complete.")
