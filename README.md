# Enhanced Text2SQL: Hybrid Graph + Vector Search for High-Accuracy NL2SQL

A text-to-SQL engine that converts natural language to SQL using **Hybrid Graph Search** (Neo4j + ChromaDB) with advanced Retrieval-Augmented Generation (RAG).

The system supports two retrieval backends:
- **Vector-only** (ChromaDB) — original semantic search with cross-encoder reranking
- **Hybrid Graph + Vector** (Neo4j + ChromaDB) — graph-aware retrieval with DDL-derived table/column nodes, entity extraction, keyword synonym expansion, and business documentation

## Whitepaper
[ResearchGate](https://www.researchgate.net/publication/389944067_Datrics_Text2SQL_A_Framework_for_Natural_Language_to_SQL_Query_Generation)
[ArXiv](https://arxiv.org/abs/2506.12234)

---

## Performance (Spider `department_store`)

### Original Test Set (27 questions)

| Metric | Vector-Only | Hybrid Graph+Vector | Improvement |
|--------|:----------:|:-------------------:|:-----------:|
| Execution Accuracy (EX) | 70.4% | **96.3%** | +25.9% |
| Exact Set Match (ESM) | 88.6% | **97.0%** | +8.4% |
| Valid SQL Rate | 85.2% | **96.3%** | +11.1% |
| Component F1 | 0.886 | **0.976** | +0.090 |
| Avg Generation Time | ~20s | ~20s | — |

### Enhanced Test Set (49 questions)

| Metric | Vector-Only | Hybrid Graph+Vector | Improvement |
|--------|:----------:|:-------------------:|:-----------:|
| Execution Accuracy (EX) | 61.8% | **89.8%** | +28.0% |
| Component F1 | 0.868 | **0.950** | +0.082 |
| Valid SQL Rate | 85.5% | **98.0%** | +12.5% |
| Avg Generation Time | ~21s | ~20s | — |

> The enhanced test set includes 22 additional questions with harder patterns (NOT EXISTS subqueries, multi-table joins, temporal conditions, aggregation with HAVING). Reports in `reports/` folder provide per-question breakdowns, structural component analysis, and failure pattern diagnostics.

---

## Architecture

### Vector-Only (t2sql/)
```
Question → ChromaDB vector search → Cross-encoder rerank → LLM generate SQL
```

### Hybrid Graph+Vector (hybridsearch/)
```
Question → Normalize (LLM)
         → Neo4j graph search (6-way fusion: vector + fulltext + entities + keywords + templates + values)
         → ChromaDB business doc search (question + entities)
         → Cross-encoder rerank
         → Table docs from Neo4j graph (DDL + descriptions + foreign keys)
         → Business context from ChromaDB (enum values, address patterns, temporal rules, metrics)
         → LLM generate SQL
         → Optional: Reflection node (different model checks syntax/executability/relevance)
```

### Neo4j Graph Schema

| Node Type | Description |
|-----------|-------------|
| `Table` | DDL, summary, purpose, dependencies, keys |
| `Column` | data_type, is_pk, is_fk, description |
| `Entity` | business concepts extracted by LLM |
| `Question` | 2048-d embedding, linked to SQL/tables/columns/entities/keywords |
| `SQL` | gold query text |
| `SQLTemplate` | parameterized SQL framework |
| `Keyword` | question keywords with synonym links |
| `Value` | enum values with LLM-inferred semantics (e.g., "PartFilled" → "Partially Filled") |

### Retrieval Pipeline (6 search dimensions)

| Search | Type | Index |
|--------|------|-------|
| Question embedding | Vector (2048-d cosine) | Neo4j VECTOR INDEX |
| Table description | Vector (2048-d cosine) | Neo4j VECTOR INDEX |
| Column description | Vector (2048-d cosine) | Neo4j VECTOR INDEX |
| Entity name | Full-text | Neo4j FULLTEXT INDEX |
| Enum value meaning | Full-text | Neo4j FULLTEXT INDEX |
| Graph traversal | Cypher (entities/keywords/SQL templates) | — |

Results fused via Reciprocal Rank Fusion (k=60), then reranked with `cross-encoder/quora-roberta-large`.

### ChromaDB Business Documentation

| Collection | Content |
|-----------|---------|
| `tables_document` | Per-table business docs (purpose, columns, aggregation, query patterns) |
| `business_document` | Enum values, address design patterns, temporal rules, global metrics |

---

## Dependencies

- Python >= 3.11
- **Docker** (for Neo4j and local databases)
- **Neo4j** (graph database, via Docker Compose)
- ChromaDB (vector store)
- LiteLLM (LLM routing)
- sentence-transformers (cross-encoder reranker)

```bash
pip install .
pip install neo4j==5.27.0
```

---

## Getting Started

### 1. Install and configure

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
pip install neo4j==5.27.0
```

Edit `descriptors/default/t2sql_descriptor.json` to set your LLM credentials.

### 2. Start Neo4j

```bash
docker compose -f hybridsearch/docker-compose.neo4j.yml up -d
```

Verify at `http://localhost:7474` (neo4j/password).

### 3. Ingest database into Neo4j

```bash
# Ingest schema + train examples into Neo4j graph
python -m hybridsearch.ingest --db department_store

# Generate and ingest business documentation into ChromaDB
python -m hybridsearch.ingest_docs
```

This creates:
- **Neo4j**: Table/Column nodes with DDL, descriptions, entities, keywords, question-SQL pairs
- **ChromaDB**: Business documentation (enum values, design patterns, aggregation rules)

### 4. Run evaluation

```bash
# Vector-only eval (original, 27 questions)
python run_eval.py --db department_store

# Graph+Vector eval (27 questions)
python -m hybridsearch.eval.graph_eval --db department_store --no-reflection

# Graph+Vector with enhanced test set (49 questions)
python -m hybridsearch.eval.graph_eval --db department_store \
    --test-file spider/department_store_enhanced_test.json --no-reflection

# Graph+Vector with reflection (different model checks SQL quality)
python -m hybridsearch.eval.graph_eval --db department_store

# Side-by-side comparison
python -m hybridsearch.eval.compare --db department_store
```

Reports are saved to `reports/` as JSON and Markdown.

---

## Evaluation Metrics

| Metric | Description |
|--------|-------------|
| **Execution Accuracy (EX)** | Fraction of questions where predicted result set exactly matches gold |
| **Exact Set Match (ESM)** | Fraction where predicted SQL executes successfully and match is exact |
| **Valid SQL Rate** | Fraction of generated SQL that executes without error |
| **Component F1** | Clause-level structural match: SELECT count, FROM tables, WHERE/HAVING/GROUP/ORDER/LIMIT presence, set operations (14 boolean fields + 4 name-level F1 scores) |
| **Name-Level F1** | Set overlap of actual table/column/aggregate names (not just counts) |
| **Result F1** | Precision/recall/F1 on result set overlap (soft metric for partial correctness) |
| **Avg Generation Time** | Wall-clock time per question (normalize → search → gen → reflect) |

### Component F1 Detail

Structural match (40%): SELECT count, SELECT distinct, table count, join count, WHERE/HAVING/GROUP/ORDER/LIMIT presence and counts, set operations.

Name-level F1 (60%): table name set overlap, SELECT column name set overlap, aggregate function set overlap, GROUP BY column set overlap.

---

## Project Structure

```
text2sql/
├── t2sql/                    # Original vector-only agent
│   ├── agent.py              # Text2SQLAgent (ChromaDB retrieval)
│   ├── base.py               # Normalize, search, generate, rerank
│   ├── evaluation/            # Metrics (compute_metrics, compare_sql_components)
│   └── vectordb/              # ChromaDB client
├── hybridsearch/              # Hybrid graph+vector agent
│   ├── agent.py               # HybridText2SQLAgent (Neo4j + ChromaDB)
│   ├── neo4j/
│   │   ├── client.py          # Neo4jClient (async driver, vector/fulltext indexes)
│   │   ├── schema.py          # Node/relationship type definitions
│   │   └── ingestor.py        # Schema ingestion + LLM semantic inference
│   ├── retrieval/
│   │   ├── hybrid_search.py   # 6-way search fusion + RRF
│   │   ├── reranker.py        # Cross-encoder reranker
│   │   └── reflection.py      # SQL quality check (different LLM model)
│   ├── vectordb/
│   │   └── chroma_client.py   # Business doc collections
│   ├── eval/
│   │   ├── graph_eval.py      # Graph-based evaluation runner
│   │   └── compare.py         # Side-by-side comparison
│   ├── ingest.py              # CLI: ingest schema + train data into Neo4j
│   ├── ingest_docs.py         # CLI: ingest business docs into ChromaDB
│   └── docker-compose.neo4j.yml
├── business_docs/             # Generated business documentation
│   ├── table_*.md             # 14 per-table docs (purpose, columns, metrics)
│   ├── enum_values.md         # Enum value → business meaning
│   ├── address_pattern.md     # Direct vs JOIN address design
│   ├── temporal_rules.md      # "currently" → date_to IS NULL, COUNT(DISTINCT)
│   └── global_metrics.md      # Cross-table KPIs
├── reports/                   # Evaluation reports (JSON + Markdown)
├── spider/                    # Spider dataset (schema + train + test + SQLite)
├── descriptors/               # LLM configuration
└── run_eval.py                # Vector-only eval entry point
```

---

## Key Features

1. **Graph-aware retrieval**: Tables, columns, entities, and keywords are nodes in Neo4j — retrieval understands relationships, not just semantic similarity.

2. **DDL as column authority**: Every table's `CREATE TABLE` DDL is stored in the graph. The LLM is instructed to use ONLY columns from DDL, eliminating hallucinations. Table docs are built from the DDL, showing every existing column with types, keys, and descriptions.

3. **Business documentation**: LLM-generated per-table docs, enum value definitions, design patterns, temporal rules, and COUNT(DISTINCT) guidance serve as reference for SQL generation.

4. **Multi-path recall**: 6 Neo4j search dimensions + 2 ChromaDB collections queried in parallel, fused via Reciprocal Rank Fusion, then reranked with cross-encoder.

5. **Reflection node**: Different LLM model checks SQL syntax (SQLite EXPLAIN), executability (execute against actual DB), and relevance (does SQL answer the question?). Retries on failure.

6. **Example filtering**: SQL examples that contradict business docs (e.g., over-joining address tables) are filtered out when relevant business context is retrieved.

---

