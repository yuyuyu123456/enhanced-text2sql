#!/usr/bin/env python3
"""Ingest business_docs markdown files into hybridsearch ChromaDB collections.

Usage:
    python -m hybridsearch.ingest_docs
    python -m hybridsearch.ingest_docs --docs-dir business_docs
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from t2sql.utils import logger
from hybridsearch.vectordb.chroma_client import HybridChromaClient


def ingest_business_docs(docs_dir: str = "business_docs") -> None:
    """Read all .md files from *docs_dir* and ingest into ChromaDB.

    - Files matching ``table_*.md`` → ``tables_document`` collection
    - All other .md files → ``business_document`` collection
    """
    if not os.path.isdir(docs_dir):
        raise FileNotFoundError(f"Docs directory not found: {docs_dir}")

    client = HybridChromaClient()
    files = sorted(f for f in os.listdir(docs_dir) if f.endswith(".md"))

    table_count = 0
    biz_count = 0

    for fname in files:
        path = os.path.join(docs_dir, fname)
        with open(path) as f:
            content = f.read()

        if fname.startswith("table_"):
            # Extract table name from filename: table_Customers.md → Customers
            table_name = fname[len("table_"):-len(".md")]
            client.add_table_doc(table_name, content)
            table_count += 1
            logger.info(f"  [tables_document] {table_name} ({len(content)} chars)")
        else:
            doc_name = fname[:-len(".md")]
            client.add_business_doc(doc_name, content)
            biz_count += 1
            logger.info(f"  [business_document] {doc_name} ({len(content)} chars)")

    logger.info(f"Done. {table_count} table docs + {biz_count} business docs ingested.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ingest business docs into hybridsearch ChromaDB")
    parser.add_argument("--docs-dir", type=str, default="business_docs")
    args = parser.parse_args()
    ingest_business_docs(args.docs_dir)


if __name__ == "__main__":
    main()
