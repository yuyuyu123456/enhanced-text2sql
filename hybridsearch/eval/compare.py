"""Side-by-side comparison: graph-based hybrid search vs vector-only retrieval.

Runs both evaluation approaches on the same test set and produces a
comparative report with deltas for each metric.
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from t2sql.utils import logger

SPIDER_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "spider")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")


async def compare_approaches(
    db_name: str,
    test_file: str | None = None,
    spider_dir: str = SPIDER_DATA_DIR,
    descriptor_path: str | None = None,
    limit: int | None = None,
    use_reflection: bool = True,
    reports_dir: str = REPORTS_DIR,
) -> dict[str, Any]:
    """Run both graph-hybrid and vector-only eval on the same test set.

    Returns:
        Dict with both metric dicts and a comparison summary.
    """
    from t2sql.evaluation.spider_eval import evaluate_spider_db
    from hybridsearch.eval.graph_eval import evaluate_with_hybrid_search

    logger.info("=" * 60)
    logger.info(f"  COMPARISON: Vector vs Graph-Hybrid on '{db_name}'")
    logger.info("=" * 60)

    # 1. Vector-only (existing ChromaDB approach)
    logger.info("\n>>> Running VECTOR-ONLY evaluation...")
    vector_metrics = await evaluate_spider_db(
        db_name=db_name,
        test_file=test_file or None,
        spider_dir=spider_dir,
        descriptor_path=descriptor_path,
        limit=limit,
    )

    # 2. Graph-hybrid approach
    logger.info("\n>>> Running GRAPH-HYBRID evaluation...")
    hybrid_metrics = await evaluate_with_hybrid_search(
        db_name=db_name,
        test_file=test_file or None,
        spider_dir=spider_dir,
        descriptor_path=descriptor_path,
        limit=limit,
        use_reflection=use_reflection,
    )

    # 3. Build comparison
    def _get(m, key, default=0.0):
        return m.get(key, default)

    comparison = {
        "db_name": db_name,
        "limit": limit,
        "use_reflection": use_reflection,
        "timestash": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "vector": {
            "execution_accuracy": _get(vector_metrics, "execution_accuracy"),
            "valid_sql_rate": _get(vector_metrics, "valid_sql_rate"),
            "avg_component_f1": _get(vector_metrics, "avg_component_f1"),
            "avg_result_f1": _get(vector_metrics, "avg_f1"),
            "avg_elapsed_ms": _get(vector_metrics, "avg_elapsed_ms"),
            "total": _get(vector_metrics, "total"),
        },
        "hybrid": {
            "execution_accuracy": _get(hybrid_metrics, "execution_accuracy"),
            "valid_sql_rate": _get(hybrid_metrics, "valid_sql_rate"),
            "avg_component_f1": _get(hybrid_metrics, "avg_component_f1"),
            "avg_result_f1": _get(hybrid_metrics, "avg_f1"),
            "avg_elapsed_ms": _get(hybrid_metrics, "avg_elapsed_ms"),
            "total": _get(hybrid_metrics, "total"),
        },
    }

    # Deltas
    deltas = {}
    for key in ["execution_accuracy", "valid_sql_rate", "avg_component_f1", "avg_result_f1"]:
        v = comparison["vector"][key]
        h = comparison["hybrid"][key]
        deltas[key] = h - v

    comparison["deltas"] = deltas
    comparison["winner"] = (
        "hybrid" if sum(deltas.values()) > 0 else "vector"
    )

    # 4. Print comparison report
    print("\n" + "=" * 70)
    print("  COMPARISON REPORT: Vector vs Graph-Hybrid")
    print("=" * 70)
    print(f"  {'Metric':<30s} {'Vector':>10s} {'Hybrid':>10s} {'Delta':>10s}")
    print("-" * 70)

    _labels = {
        "execution_accuracy": "Execution Accuracy",
        "valid_sql_rate": "Valid SQL Rate",
        "avg_component_f1": "Component F1",
        "avg_result_f1": "Result F1",
    }
    for key, label in _labels.items():
        v = comparison["vector"][key]
        h = comparison["hybrid"][key]
        d = deltas[key]
        sign = "+" if d >= 0 else ""
        print(f"  {label:<30s} {v:>10.1%} {h:>10.1%} {sign}{d:>9.1%}")

    print("-" * 70)
    print(f"  {'Avg Generation Time':<30s} "
          f"{comparison['vector']['avg_elapsed_ms']:>10.0f}ms "
          f"{comparison['hybrid']['avg_elapsed_ms']:>10.0f}ms")
    print("=" * 70)
    print(f"  Winner: {comparison['winner'].upper()}")
    print("=" * 70)

    # 5. Save comparison report
    os.makedirs(reports_dir, exist_ok=True)
    ts = comparison["timestash"]
    report_path = os.path.join(reports_dir, f"comparison_{db_name}_{ts}.json")

    import json as _json
    with open(report_path, "w") as f:
        _json.dump(comparison, f, indent=2, default=str, ensure_ascii=False)
    logger.info(f"Comparison report saved to: {report_path}")

    # Markdown version
    md_path = os.path.join(reports_dir, f"comparison_{db_name}_{ts}.md")
    _write_comparison_md(comparison, md_path)
    logger.info(f"Comparison markdown saved to: {md_path}")

    return comparison


def _write_comparison_md(comp: dict, path: str) -> None:
    """Write a Markdown comparison report."""
    lines = [
        f"# Comparison Report: Vector vs Graph-Hybrid",
        "",
        f"**Database:** `{comp['db_name']}`",
        f"**Date:** {comp['timestash']}",
        f"**Reflection:** {comp['use_reflection']}",
        f"**Winner:** **{comp['winner'].upper()}**",
        "",
        "---",
        "",
        "## Metrics",
        "",
        "| Metric | Vector | Hybrid | Delta |",
        "|--------|--------|--------|-------|",
    ]
    _labels = {
        "execution_accuracy": "Execution Accuracy",
        "valid_sql_rate": "Valid SQL Rate",
        "avg_component_f1": "Component F1",
        "avg_result_f1": "Result F1",
    }
    for key, label in _labels.items():
        v = comp["vector"][key]
        h = comp["hybrid"][key]
        d = comp["deltas"][key]
        sign = "+" if d >= 0 else ""
        lines.append(f"| {label} | {v:.1%} | {h:.1%} | {sign}{d:.1%} |")

    lines.extend([
        "",
        "---",
        "",
        "## Latency",
        "",
        f"| Approach | Avg Time |",
        f"|----------|----------|",
        f"| Vector | {comp['vector']['avg_elapsed_ms']:.0f} ms |",
        f"| Hybrid | {comp['hybrid']['avg_elapsed_ms']:.0f} ms |",
        "",
    ])

    with open(path, "w") as f:
        f.write("\n".join(lines))


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare vector vs graph-hybrid text-to-SQL evaluation"
    )
    parser.add_argument("--db", type=str, required=True)
    parser.add_argument("--test-file", type=str, default=None,
                        help="Path to test JSON (default: spider_dir/{db}_test.json)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-reflection", action="store_true")

    args = parser.parse_args()
    asyncio.run(
        compare_approaches(
            db_name=args.db,
            test_file=args.test_file,
            limit=args.limit,
            use_reflection=not args.no_reflection,
        )
    )


if __name__ == "__main__":
    main()
