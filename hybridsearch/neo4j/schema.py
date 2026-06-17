"""Graph schema constants and Pydantic models for the Neo4j hybrid search system.

Defines all node labels, relationship types, and property schemas used to
model the database schema, training examples, entities, and keywords.
"""

from pydantic import BaseModel


# ── Node Labels ──
NODE_TABLE = "Table"
NODE_COLUMN = "Column"
NODE_ENTITY = "Entity"
NODE_QUESTION = "Question"
NODE_SQL = "SQL"
NODE_SQL_TEMPLATE = "SQLTemplate"
NODE_KEYWORD = "Keyword"
NODE_VALUE = "Value"
NODE_BUSINESS_DOC = "BusinessDoc"    # per-table business documentation
NODE_GLOBAL_METRIC = "GlobalMetric"  # cross-table aggregation metric
NODE_ENUM_DOC = "EnumDoc"            # enum column value definitions

# ── Relationship Types ──
REL_HAS_COLUMN = "HAS_COLUMN"
REL_HAS_PRIMARY_KEY = "HAS_PRIMARY_KEY"
REL_HAS_FOREIGN_KEY = "HAS_FOREIGN_KEY"
REL_MAPS_TO = "MAPS_TO"
REL_USES_TABLE = "USES_TABLE"
REL_USES_COLUMN = "USES_COLUMN"
REL_HAS_ENTITY = "HAS_ENTITY"
REL_HAS_SQL = "HAS_SQL"
REL_INSTANTIATES = "INSTANTIATES"
REL_HAS_KEYWORD = "HAS_KEYWORD"
REL_SYNONYM_OF = "SYNONYM_OF"
REL_CONNECTED_TO = "CONNECTED_TO"    # table-to-table dependency (LLM-inferred)
REL_BELONGS_TO = "BELONGS_TO"      # entity-to-table (which table the entity belongs to)
REL_HAS_VALUE = "HAS_VALUE"        # column-to-enum-value
REL_HAS_BUSINESS_DOC = "HAS_BUSINESS_DOC"    # table-to-business-doc
REL_HAS_METRIC = "HAS_METRIC"                # table/metric-to-metric
REL_DOCUMENTS_COLUMN = "DOCUMENTS_COLUMN"    # business-doc-to-column
REL_DOCUMENTS_ENUM = "DOCUMENTS_ENUM"        # enum-doc-to-enum-values
REL_HAS_ENTITY = "HAS_ENTITY"

# ── Relationship Properties ──
REL_PROPS_FOREIGN_KEY = ["ref_table", "ref_column"]
REL_PROPS_CONNECTED_TO = ["keys"]    # shared key columns


# ── Pydantic Node Property Models ──

class TableNode(BaseModel):
    name: str
    type: str = "unknown"  # "fact", "dimension", "junction"


class ColumnNode(BaseModel):
    name: str
    table: str
    data_type: str = "VARCHAR"
    is_pk: bool = False
    is_fk: bool = False


class EntityNode(BaseModel):
    name: str


class QuestionNode(BaseModel):
    text: str
    db_id: str | None = None
    embedding: list[float] | None = None  # stored as list property for Neo4j vector index


class SQLNode(BaseModel):
    text: str
    db_id: str | None = None


class SQLTemplateNode(BaseModel):
    template: str


class KeywordNode(BaseModel):
    word: str
