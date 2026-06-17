#!/usr/bin/env python3
"""Ingest Spider dataset into Neo4j graph database.

Usage:
    # 1. Start Neo4j:
    docker compose -f hybridsearch/docker-compose.neo4j.yml up -d

    # 2. Ingest department_store schema + train data:
    python -m hybridsearch.ingest --db department_store

    # 3. With custom Neo4j connection:
    python -m hybridsearch.ingest --db department_store --neo4j-uri bolt://localhost:7687 --neo4j-user neo4j --neo4j-password mypass

    # 4. Force re-ingestion (clears existing data):
    python -m hybridsearch.ingest --db department_store --clear
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from t2sql.utils import logger
from t2sql.agent import get_sql_agent

SPIDER_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "spider")


async def ingest_database(
    db_name: str,
    spider_dir: str = SPIDER_DATA_DIR,
    descriptor_path: str | None = None,
    neo4j_uri: str = "bolt://localhost:7687",
    neo4j_user: str = "neo4j",
    neo4j_password: str = "password",
    clear_first: bool = True,
    limit: int | None = None,
) -> None:
    """Ingest schema + training data for a Spider database into Neo4j.

    Args:
        db_name: Spider database name (e.g., 'department_store').
        spider_dir: Path to the Spider dataset directory.
        descriptor_path: Optional path to descriptor config.
        neo4j_uri: Neo4j Bolt URI.
        neo4j_user: Neo4j username.
        neo4j_password: Neo4j password.
        clear_first: If True, clear existing graph data first.
    """
    from hybridsearch.neo4j.client import Neo4jClient
    from hybridsearch.neo4j.ingestor import GraphIngestor

    # Paths
    schema_path = os.path.join(spider_dir, "schema.sql")
    train_file = os.path.join(spider_dir, f"{db_name}_train.json")

    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    if not os.path.exists(train_file):
        raise FileNotFoundError(f"Train data not found: {train_file}")

    with open(train_file) as f:
        train_data = json.load(f)

    if limit:
        train_data = train_data[:limit]

    logger.info(f"=== Ingesting '{db_name}' into Neo4j ===")
    logger.info(f"Schema: {schema_path}")
    logger.info(f"Training pairs: {len(train_data)}")

    # Get the LLM router from the existing agent
    agent = get_sql_agent(descriptor_path)
    router = agent._router

    # Initialize Neo4j client and ingestor
    client = Neo4jClient(uri=neo4j_uri, user=neo4j_user, password=neo4j_password)

    if not await client.verify_connectivity():
        raise ConnectionError(
            f"Cannot connect to Neo4j at {neo4j_uri}. "
            f"Is the Docker container running?"
        )
    logger.info("Neo4j connection verified.")

    ingestor = GraphIngestor(client=client, router=router, default_model="glm-4-flash")

    await ingestor.ingest_all(
        schema_path=schema_path,
        db_name=db_name,
        train_data=train_data,
        clear_first=clear_first,
    )

    await client.close()
    logger.info("Done.")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingest Spider dataset into Neo4j graph"
    )
    parser.add_argument("--db", type=str, required=True, help="Spider database name")
    parser.add_argument("--spider-dir", type=str, default=SPIDER_DATA_DIR)
    parser.add_argument("--descriptor", type=str, default=None)
    parser.add_argument("--neo4j-uri", type=str, default="bolt://localhost:7687")
    parser.add_argument("--neo4j-user", type=str, default="neo4j")
    parser.add_argument("--neo4j-password", type=str, default="password")
    parser.add_argument("--no-clear", action="store_true", help="Skip clearing data")
    parser.add_argument("--limit", type=int, default=None, help="Limit train examples")

    args = parser.parse_args()

    asyncio.run(
        ingest_database(
            db_name=args.db,
            spider_dir=args.spider_dir,
            descriptor_path=args.descriptor,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            clear_first=not args.no_clear,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
