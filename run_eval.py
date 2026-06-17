#!/usr/bin/env python3
"""Run text-to-SQL evaluation on the Spider dataset.

Usage:
    # 1. First, ingest the schema and training examples:
    python scripts/ingest_spider.py --db department_store

    # 2. Then, evaluate:
    python run_eval.py --db department_store
    python run_eval.py --db department_store --limit 5   # quick test
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from t2sql.evaluation.spider_eval import evaluate_spider_db, main

if __name__ == "__main__":
    main()