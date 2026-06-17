import hashlib
import json
import ast
import uuid
import logging
import os
import numpy as np
from datetime import datetime
from t2sql.prompts import DEFAULT_PROMPTS, DEFAULT_SQL_INSTRUCTIONS
import copy
from sshtunnel import SSHTunnelForwarder


"""Setup logging configuration."""
# Create logs directory if it doesn't exist
if not os.path.exists("logs"):
    os.makedirs("logs")

# Configure logging
logger = logging.getLogger("text2sql")
logger.setLevel(logging.INFO)

# Create file handler
log_filename = f"logs/text2sql_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
file_handler = logging.FileHandler(log_filename)
file_handler.setLevel(logging.INFO)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

DEFAULT_DESCRIPTOR_FILE_NAME: str = "t2sql_descriptor.json"


DEFAULT_BUSINESS_RULES = [

]


def parse_json(text: str) -> dict | list | None:
    try:
        result = json.loads(text.split("```json")[-1].split("```")[0])
    except:
        try:
            result = ast.literal_eval(text.split("```json")[-1].split("```")[0])
        except:
            return None
    # If caller expects a dict (via ** unpacking), return empty dict instead of list/None
    if result is None:
        return {}
    return result if isinstance(result, (dict, list)) else {}


def parse_code(text: str) -> str | None:
    try:
        result = (
            text.split("```sql")[-1]
            .split("```")[0]
            .replace("<code>", "")
            .replace("</code>", "")
        )
    except:
        try:
            result = ast.literal_eval(
                text.split("```sql")[-1]
                .split("```")[0]
                .replace("<code>", "")
                .replace("</code>", "")
            )
        except:
            return None
    return result


def deterministic_uuid(content: str | bytes) -> str:
    """Creates deterministic UUID on hash value of string or byte content.

    Args:
        content: String or byte representation of data.

    Returns:
        UUID of the content.
    """
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    elif isinstance(content, bytes):
        content_bytes = content
    else:
        raise ValueError(f"Content type {type(content)} not supported !")

    hash_object = hashlib.sha256(content_bytes)
    hash_hex = hash_object.hexdigest()
    namespace = uuid.UUID("00000000-0000-0000-0000-000000000000")
    content_uuid = str(uuid.uuid5(namespace, hash_hex))

    return content_uuid


def load_examples(path: str) -> list[dict]:
    """Load examples JSON file."""
    files = os.listdir(path)
    results = []

    for filename in files:
        input_path = os.path.join(path, filename)

        with open(input_path, "r") as file:
            state = json.load(file)
            results.append({"question": state.get("question"), "sql": state.get("sql")})

    return results


def load_prompts(path: str) -> dict[str]:
    """Load prompts from JSON file."""
    filename = os.path.join(path, DEFAULT_DESCRIPTOR_FILE_NAME)
    try:
        with open(filename, "r", encoding="utf-8") as f:
            prompts = json.load(f).get("prompts")
            used_prompts = copy.deepcopy(DEFAULT_PROMPTS)
            for prompt_name, prompt in prompts.items():
                used_prompts[prompt_name] = prompt
            return used_prompts
    except Exception as e:
        logger.warning(f"Error loading prompts: {e}, loading default prompts")
        return DEFAULT_PROMPTS


def calculate_threshold(n: int) -> int:
    """
    Calculate the threshold for table filtering based on count.

    Args:
        n (int): Number of samples

    Returns:
        int: Calculated threshold value
    """
    if n > 6:
        return np.ceil(n / 2)
    elif n in [5, 6]:
        return np.floor(n / 2)
    elif n == 4:
        return 1
    return 0


def create_default_descriptor(descriptor_base_path: str) -> dict:
    descriptor = {
        "router_model_list": [
            {
                "model_name": os.getenv("AZURE_API_DEFAULT_MODEL", "gpt-4o-2024-11-20"),
                "litellm_params": {
                    "model": f"azure/{os.getenv('AZURE_API_DEFAULT_MODEL', 'gpt-4o-2024-11-20')}",
                    "api_key": os.getenv("AZURE_API_KEY"),
                    "api_version": os.getenv("AZURE_API_VERSION"),
                    "api_base": os.getenv("AZURE_API_BASE"),
                },
            },
            {
                "model_name": "o3-mini",
                "litellm_params": {
                    "model": "o3-mini",
                    "api_key": os.getenv("OPENAI_API_KEY"),
                },
            },
            {
                "model_name": "o1-mini",
                "litellm_params": {
                    "model": "o1-mini",
                    "api_key": os.getenv("OPENAI_API_KEY"),
                },
            },
        ],
        "open_ai_key": os.getenv("OPENAI_API_KEY"),
        "embedding_api_key": os.getenv("EMBEDDING_API_KEY"),
        "embedding_api_base": os.getenv("EMBEDDING_API_BASE"),
        "embedding_model_name": os.getenv("EMBEDDING_MODEL_NAME"),
        "model": os.getenv("AZURE_API_DEFAULT_MODEL", "gpt-4o-2024-11-20"),
        "descriptors_path": "descriptors/default",
        "docs_md_folder": "training_data_storage/md_docs",
        "docs_json_folder": "training_data_storage/json_docs",
        "examples_folder": "training_data_storage/examples",
        "examples_extended_folder": "training_data_storage/train_examples",
        "docs_ddl_folder": "training_data_storage/ddl_docs",
        "router_default_max_parallel_requests": 20,
        "router_default_num_retries": 3,
        "db_path": "vector_db_storage",
        "collection_metadata": {"hnsw:space": "cosine"},
        "n_results_sql": 15,
        "client": "persistent",
        "business_rules": DEFAULT_BUSINESS_RULES,
        "prompts": {"DEFAULT_SQL_INSTRUCTIONS": DEFAULT_SQL_INSTRUCTIONS},
        "db": {
            "source": "postgres",
            "connection_config": {
                "schema": "public",
                "password": "postgres",
                "host": "localhost",
                "database": "dvdrental",
                "user": "postgres",
                "port": 5433,
            },
        },
    }

    with open(
        os.path.join(descriptor_base_path, DEFAULT_DESCRIPTOR_FILE_NAME), "w"
    ) as f:
        f.write(json.dumps(descriptor))

    return descriptor


def get_config(descriptor_base_path: str | None = None):
    if descriptor_base_path is None:
        descriptor_base_path = "default"

        descriptors_folder = os.getenv("T2SQL_DESCRIPTORS_FOLDER", "descriptors")
        if not os.path.exists(descriptors_folder):
            os.mkdir(descriptors_folder)

        descriptor_base_path = os.path.join(descriptors_folder, descriptor_base_path)

    if not os.path.exists(descriptor_base_path):
        os.mkdir(descriptor_base_path)

    descriptor_path = os.path.join(descriptor_base_path, DEFAULT_DESCRIPTOR_FILE_NAME)
    if os.path.exists(descriptor_path):
        with open(descriptor_path, "r") as f:
            descriptor = json.loads(f.read())
    else:
        descriptor = create_default_descriptor(descriptor_base_path)

    if descriptor.get("ssh_tunnel"):
        tunnel = SSHTunnelForwarder(
            descriptor["ssh_tunnel"]["host"],
            ssh_username=descriptor["ssh_tunnel"]["username"],
            ssh_pkey=descriptor["ssh_tunnel"]["private_key_path"],
            remote_bind_address=(
                descriptor["db"]["connection_config"]["host"],
                descriptor["db"]["connection_config"]["port"],
            ),
        )
        tunnel.start()
        descriptor["db"]["connection_config"]["host"] = (
            "127.0.0.1"  # Connect to tunnel locally
        )
        descriptor["db"]["connection_config"]["port"] = tunnel.local_bind_port

    descriptor["descriptors_folder"] = descriptor_base_path

    return descriptor


def parse_schema_sql(
    schema_path: str, db_name: str = "default", schema_name: str = "main"
) -> "pd.DataFrame":
    """Parse a schema.sql file and return a DataFrame matching INFORMATION_SCHEMA format.

    Extracts CREATE TABLE statements to get table names, column names, data types,
    primary keys, and foreign key relationships.

    Args:
        schema_path: Path to the schema.sql file.
        db_name: Database catalog name to use (default: "default").
        schema_name: Schema name to use (default: "main").

    Returns:
        pd.DataFrame with columns: table_catalog, table_schema, table_name,
        column_name, data_type, is_pk, fk_table, fk_column.
    """
    import re
    import pandas as pd

    with open(schema_path, "r") as f:
        content = f.read()

    rows = []
    # Match CREATE TABLE statements — capture table name and body
    table_pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?(\w+)[`\"']?\s*\((.*?)\)\s*;",
        re.IGNORECASE | re.DOTALL,
    )

    for match in table_pattern.finditer(content):
        table_name = match.group(1)
        body = match.group(2)

        # Parse foreign keys from this table's body
        fk_map = {}  # column -> (ref_table, ref_column)
        fk_pattern = re.compile(
            r"FOREIGN\s+KEY\s*\(\s*[`\"']?(\w+)[`\"']?\s*\)\s*REFERENCES\s+[`\"']?(\w+)[`\"']?\s*\(\s*[`\"']?(\w+)[`\"']?\s*\)",
            re.IGNORECASE,
        )
        for fk_match in fk_pattern.finditer(body):
            fk_map[fk_match.group(1)] = (fk_match.group(2), fk_match.group(3))

        # Parse primary key
        pk_columns = set()
        pk_pattern = re.compile(
            r"PRIMARY\s+KEY\s*\(\s*([^)]+)\s*\)", re.IGNORECASE
        )
        for pk_match in pk_pattern.finditer(body):
            cols = re.findall(r"[`\"']?(\w+)[`\"']?", pk_match.group(1))
            pk_columns.update(cols)

        # Parse individual column definitions
        # Remove constraint lines (PRIMARY KEY, FOREIGN KEY, CONSTRAINT, CHECK, UNIQUE)
        body_cleaned = re.sub(
            r"^\s*(PRIMARY|FOREIGN|CONSTRAINT|CHECK|UNIQUE)\s.*$",
            "",
            body,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        col_pattern = re.compile(
            r"^\s*[`\"']?(\w+)[`\"']?\s+(\w+(?:\s*\([^)]*\))?)"
            r"(?:\s+(?:NOT\s+NULL|NULL|AUTO_INCREMENT|AUTOINCREMENT|PRIMARY\s+KEY|UNIQUE|DEFAULT\s+\S+))*",
            re.MULTILINE | re.IGNORECASE,
        )
        for col_match in col_pattern.finditer(body_cleaned):
            col_name = col_match.group(1)
            col_type = col_match.group(2).strip()
            col_full = col_match.group(0)
            # Check inline PRIMARY KEY
            is_pk = col_name in pk_columns or bool(
                re.search(r"\bPRIMARY\s+KEY\b", col_full, re.IGNORECASE)
            )
            fk_table, fk_column = fk_map.get(col_name, (None, None))

            rows.append({
                "table_catalog": db_name,
                "table_schema": schema_name,
                "table_name": table_name,
                "column_name": col_name,
                "data_type": col_type,
                "is_pk": is_pk,
                "fk_table": fk_table,
                "fk_column": fk_column,
            })

    df = pd.DataFrame(rows)
    logger.info(
        f"Parsed {len(df)} columns across {df['table_name'].nunique()} tables "
        f"from {schema_path}"
    )
    return df
