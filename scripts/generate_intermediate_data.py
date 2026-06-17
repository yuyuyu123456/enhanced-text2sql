#!/usr/bin/env python3
"""Generate intermediate data (json_docs + train_examples) from existing artifacts.

Root cause of empty directories:
  1. json_docs: train_from_schema_df() ingests directly into the vector DB but never
     persists the intermediate JSON to disk. generate_json_from_md_documentation()
     is the method that writes JSON files, but ingest_spider.py never calls it.
  2. train_examples: expand_examples_structure() requires examples/examples.json
     to exist, but the examples/ folder is empty. The Spider ingestion script calls
     learn_sql() directly (vector DB only) without creating extended examples.

Usage:
    python scripts/generate_intermediate_data.py --db department_store
    python scripts/generate_intermediate_data.py --db department_store --skip-json-docs
    python scripts/generate_intermediate_data.py --db department_store --skip-train-examples
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from t2sql.agent import get_sql_agent
from t2sql.utils import logger

SPIDER_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "spider")


async def generate_json_docs(agent) -> int:
    """Generate json_docs from existing md_docs using LLM processing.

    Reads each .md file from md_docs/, runs learn_md_document() to extract
    structured info via LLM, and saves the result as .json in json_docs/.
    """
    logger.info("=" * 60)
    logger.info("STEP 1: Generating json_docs from md_docs...")
    logger.info("=" * 60)
    await agent.generate_json_from_md_documentation()

    # Verify
    json_dir = agent._docs_json_folder
    if json_dir and os.path.exists(json_dir):
        files = [f for f in os.listdir(json_dir) if f.endswith(".json")]
        logger.info(f"json_docs generated: {len(files)} files in {json_dir}")
        return len(files)
    return 0


async def generate_train_examples(agent, db_name: str) -> int:
    """Generate train_examples from Spider training data.

    1. Creates examples/examples.json from Spider {db_name}_train.json
    2. Runs expand_examples_structure() to enrich each example with table
       selection rationale and save to train_examples/.
    """
    logger.info("=" * 60)
    logger.info("STEP 2: Generating train_examples from Spider training data...")
    logger.info("=" * 60)

    # 1. Create examples/examples.json from Spider training data
    examples_dir = agent._examples_folder
    if examples_dir is None:
        raise ValueError("examples_folder not configured in descriptor")

    os.makedirs(examples_dir, exist_ok=True)
    examples_path = os.path.join(examples_dir, "examples.json")

    train_file = os.path.join(SPIDER_DATA_DIR, f"{db_name}_train.json")
    if not os.path.exists(train_file):
        logger.error(f"Training data not found: {train_file}")
        return 0

    with open(train_file) as f:
        train_data = json.load(f)

    # Convert Spider format to examples format: {question, sql}
    examples = []
    for item in train_data:
        examples.append({
            "question": item["question"],
            "sql": item["query"],
        })

    with open(examples_path, "w") as f:
        json.dump(examples, f, indent=2, ensure_ascii=False)

    logger.info(
        f"Created examples/examples.json with {len(examples)} examples "
        f"from {db_name}_train.json"
    )

    # 2. Expand examples structure → saves to train_examples/
    logger.info("Expanding examples structure (LLM calls for table selection)...")
    await agent.expand_examples_structure()

    # Verify
    ext_dir = agent._examples_extended_folder
    if ext_dir and os.path.exists(ext_dir):
        files = [f for f in os.listdir(ext_dir) if f.endswith(".json")]
        logger.info(f"train_examples generated: {len(files)} files in {ext_dir}")
        return len(files)
    return 0


async def main_async(db_name: str, skip_json_docs: bool, skip_train_examples: bool):
    logger.info(f"=== Generating intermediate data for: {db_name} ===")

    agent = get_sql_agent()

    results = {}

    if not skip_json_docs:
        n_json = await generate_json_docs(agent)
        results["json_docs"] = n_json
    else:
        logger.info("Skipping json_docs generation.")

    if not skip_train_examples:
        n_examples = await generate_train_examples(agent, db_name)
        results["train_examples"] = n_examples
    else:
        logger.info("Skipping train_examples generation.")

    logger.info("=" * 60)
    logger.info("=== Generation complete ===")
    for key, count in results.items():
        logger.info(f"  {key}: {count} files generated")
    logger.info("=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate intermediate data (json_docs + train_examples)"
    )
    parser.add_argument(
        "--db", type=str, required=True, help="Spider database name (e.g., department_store)"
    )
    parser.add_argument(
        "--skip-json-docs", action="store_true", help="Skip json_docs generation"
    )
    parser.add_argument(
        "--skip-train-examples", action="store_true", help="Skip train_examples generation"
    )

    args = parser.parse_args()
    asyncio.run(
        main_async(
            db_name=args.db,
            skip_json_docs=args.skip_json_docs,
            skip_train_examples=args.skip_train_examples,
        )
    )


if __name__ == "__main__":
    main()
