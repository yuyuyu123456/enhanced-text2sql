"""Graph-based evaluation runner — tests hybrid search retrieval accuracy.

Evaluates text-to-SQL using ``HybridText2SQLAgent`` which combines Neo4j graph
retrieval with vector and full-text search.
"""

import asyncio
import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from t2sql.evaluation.metrics import (
    compare_result_sets,
    compare_result_sets_intersection,
    compare_sql_components,
    compute_metrics,
    print_evaluation_report,
)
from t2sql.evaluation.spider_eval import execute_sql_on_sqlite, save_evaluation_report
from t2sql.utils import logger, get_config

from hybridsearch.neo4j.client import Neo4jClient
from hybridsearch.agent import HybridText2SQLAgent

SPIDER_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "spider")


async def evaluate_with_hybrid_search(
    db_name: str,
    test_file: str | None = None,
    spider_dir: str = SPIDER_DATA_DIR,
    descriptor_path: str | None = None,
    limit: int | None = None,
    use_reflection: bool = True,
    neo4j_uri: str = "bolt://localhost:7687",
    generation_model: str = "glm-4-flash",
    reflection_model: str = "glm-4-plus",
) -> dict[str, Any]:
    """Evaluate text-to-SQL using ``HybridText2SQLAgent`` directly.

    The agent handles the full pipeline: normalize → hybrid search →
    rerank → table docs from graph → entity/value hints → generate SQL →
    reflect and retry.  The eval only executes the generated SQL and
    compares results.
    """
    schema_path = os.path.join(spider_dir, "schema.sql")
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema not found: {schema_path}")

    if test_file is None:
        test_file = os.path.join(spider_dir, f"{db_name}_test.json")
    if not os.path.exists(test_file):
        raise FileNotFoundError(f"Test file not found: {test_file}")

    with open(test_file) as f:
        test_data = json.load(f)
    if limit:
        test_data = test_data[:limit]

    logger.info(f"=== Graph-Hybrid Eval: {db_name} ===")
    logger.info(f"Test questions: {len(test_data)}")

    # Initialize agent
    config = get_config(descriptor_path)
    neo4j = Neo4jClient(uri=neo4j_uri)
    if not await neo4j.verify_connectivity():
        raise ConnectionError(f"Cannot connect to Neo4j at {neo4j_uri}")

    agent = HybridText2SQLAgent(
        config=config,
        neo4j_client=neo4j,
        generation_model=generation_model,
        reflection_model=reflection_model,
        use_reflection=use_reflection,
    )

    results = []
    for i, item in enumerate(test_data):
        question = item["question"]
        gold_sql = item["query"]
        gold_sql_struct = item.get("sql", {})

        result = {
            "question": question,
            "gold_sql": gold_sql,
            "pred_sql": None,
            "executed": False,
            "match": False,
            "error": None,
            "search_source": "hybrid_graph",
        }

        t0 = time.perf_counter()
        try:
            pred_sql, step = await agent.make_sql(question)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            pred_sql = pred_sql.replace("public.", "")
            result["pred_sql"] = pred_sql
            result["elapsed_ms"] = elapsed_ms
            result["step"] = step
            result["components"] = compare_sql_components(pred_sql, gold_sql_struct, gold_sql)

        except Exception as e:
            result["error"] = f"SQL generation failed: {e}"
            results.append(result)
            status_line = f"[E] {question[:60]}... ({e})"
            logger.info(f"  [{i+1}/{len(test_data)}] {status_line}")
            continue

        # Execute and compare
        try:
            df_gold = execute_sql_on_sqlite(schema_path, gold_sql)
        except Exception as e:
            result["error"] = f"Gold SQL execution failed: {e}"
            results.append(result)
            continue

        try:
            df_pred = execute_sql_on_sqlite(schema_path, pred_sql)
            result["executed"] = True
        except Exception as e:
            result["error"] = f"Pred SQL execution failed: {e}"
            results.append(result)
            continue

        try:
            result["match"] = compare_result_sets(df_pred, df_gold)
            result["intersection"] = compare_result_sets_intersection(df_pred, df_gold)
        except Exception as e:
            result["error"] = f"Result comparison failed: {e}"

        results.append(result)
        status = "✓" if result["match"] else ("E" if not result["executed"] else "✗")
        comp_f1 = result.get("components", {}).get("component_f1", 0)
        logger.info(
            f"  [{i+1}/{len(test_data)}] [{status}] {question[:50]}..."
            f"  {elapsed_ms:.0f}ms compF1={comp_f1:.2f}"
        )

    metrics = compute_metrics(results)
    print_evaluation_report(metrics)

    report_path = save_evaluation_report(metrics, f"{db_name}_graph_hybrid")
    logger.info(f"Report saved to: {report_path}")

    await agent.close()
    return metrics


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate text-to-SQL with graph-based hybrid search"
    )
    parser.add_argument("--db", type=str, required=True, help="Spider database name")
    parser.add_argument("--test-file", type=str, default=None,
                        help="Path to test JSON (default: spider_dir/{db}_test.json)")
    parser.add_argument("--limit", type=int, default=None, help="Limit test questions")
    parser.add_argument("--no-reflection", action="store_true", help="Disable reflection")
    parser.add_argument("--neo4j-uri", type=str, default="bolt://localhost:7687")
    parser.add_argument("--generation-model", type=str, default="glm-4-flash")
    parser.add_argument("--reflection-model", type=str, default="glm-4-plus")

    args = parser.parse_args()
    asyncio.run(
        evaluate_with_hybrid_search(
            db_name=args.db,
            test_file=args.test_file,
            limit=args.limit,
            use_reflection=not args.no_reflection,
            neo4j_uri=args.neo4j_uri,
            generation_model=args.generation_model,
            reflection_model=args.reflection_model,
        )
    )


if __name__ == "__main__":
    main()
