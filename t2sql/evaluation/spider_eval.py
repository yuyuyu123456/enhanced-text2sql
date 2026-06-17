"""Spider dataset evaluation runner.

Evaluates text-to-SQL performance on a Spider database by executing both
generated and gold SQL against the original SQLite database, then comparing results.
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from t2sql.agent import get_sql_agent
from t2sql.evaluation.metrics import (
    compare_result_sets,
    compare_result_sets_intersection,
    compare_sql_components,
    compute_metrics,
    print_evaluation_report,
)
from t2sql.utils import logger


SPIDER_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "spider")


def execute_sql_on_sqlite(schema_path: str, sql: str) -> pd.DataFrame:
    """Execute a SQL query against a Spider SQLite database.

    Args:
        schema_path: Path to the directory containing the .sqlite file.
        sql: SQL query to execute.

    Returns:
        pd.DataFrame with query results.

    Raises:
        Exception: If the query fails.
    """
    # Find the .sqlite file
    db_dir = os.path.dirname(schema_path)
    sqlite_files = [
        f for f in os.listdir(db_dir) if f.endswith(".sqlite")
    ]
    if not sqlite_files:
        raise FileNotFoundError(f"No .sqlite file found in {db_dir}")

    db_path = os.path.join(db_dir, sqlite_files[0])
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(sql, conn)
        return df
    finally:
        conn.close()


async def evaluate_single_question(
    agent,
    item: dict[str, Any],
    schema_path: str,
) -> dict[str, Any]:
    """Evaluate a single Spider test question.

    Args:
        agent: Text2SQLAgent instance.
        item: Spider item with 'question' and 'query' (gold SQL).
        schema_path: Path to schema.sql for the database.

    Returns:
        Dict with evaluation result for this question.
    """
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
    }

    # 1. Generate SQL using the agent
    try:
        t0 = time.perf_counter()
        pred_sql, step = await agent.make_sql(question)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # Normalize: strip "public." schema prefix for SQLite compatibility
        pred_sql = pred_sql.replace("public.", "")
        result["pred_sql"] = pred_sql
        result["step"] = step
        result["elapsed_ms"] = elapsed_ms
        # SQL component comparison against gold 'sql' structure + gold query string
        result["components"] = compare_sql_components(pred_sql, gold_sql_struct, gold_sql)
    except Exception as e:
        result["error"] = f"SQL generation failed: {e}"
        return result

    # 2. Execute gold SQL on SQLite
    try:
        df_gold = execute_sql_on_sqlite(schema_path, gold_sql)
    except Exception as e:
        result["error"] = f"Gold SQL execution failed: {e}"
        logger.warning(f"Gold SQL failed for '{question[:60]}...': {e}")
        # Can't compare if gold fails — skip
        return result

    # 3. Execute predicted SQL on SQLite
    try:
        df_pred = execute_sql_on_sqlite(schema_path, pred_sql)
        result["executed"] = True
    except Exception as e:
        result["error"] = f"Pred SQL execution failed: {e}"
        return result

    # 4. Compare results
    try:
        result["match"] = compare_result_sets(df_pred, df_gold)
        result["intersection"] = compare_result_sets_intersection(df_pred, df_gold)
    except Exception as e:
        result["error"] = f"Result comparison failed: {e}"

    return result


async def evaluate_spider_db(
    db_name: str,
    test_file: str | None = None,
    spider_dir: str = SPIDER_DATA_DIR,
    descriptor_path: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Evaluate text-to-SQL on a Spider database.

    Args:
        db_name: Spider database name (e.g., "department_store").
        test_file: Path to test JSON file (question/SQL pairs).
                   Defaults to spider_dir/department_store_enhanced_test.json.
        spider_dir: Root directory of the Spider dataset.
        descriptor_path: Optional path to descriptor config.
        limit: Max number of test questions to evaluate.

    Returns:
        Dict with aggregate metrics and per-question results.
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

    logger.info(f"=== Evaluating Spider DB: {db_name} ===")
    logger.info(f"Test questions: {len(test_data)}")

    agent = get_sql_agent(descriptor_path)

    results = []
    for i, item in enumerate(test_data):
        question = item["question"]
        logger.info(
            f"[{i+1}/{len(test_data)}] Evaluating: {question[:70]}..."
        )
        result = await evaluate_single_question(agent, item, schema_path)
        results.append(result)

        status = "✓" if result["match"] else ("E" if not result["executed"] else "✗")
        logger.info(f"  [{status}] step={result.get('step', 'N/A')}")

    metrics = compute_metrics(results)
    print_evaluation_report(metrics)

    # Save report to reports/ folder
    report_path = save_evaluation_report(metrics, db_name)
    logger.info(f"Report saved to: {report_path}")

    return metrics


REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "reports")


def save_evaluation_report(
    metrics: dict[str, Any],
    db_name: str,
    reports_dir: str = REPORTS_DIR,
) -> str:
    """Save evaluation report as JSON and Markdown to the reports directory.

    Args:
        metrics: Aggregate metrics dict from compute_metrics().
        db_name: Name of the evaluated database.
        reports_dir: Path to the reports directory.

    Returns:
        Path to the saved JSON report file.
    """
    os.makedirs(reports_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"eval_{db_name}_{timestamp}"

    # --- JSON report (full, machine-readable) ---
    json_path = os.path.join(reports_dir, f"{base_name}.json")
    with open(json_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str, ensure_ascii=False)

    # --- Markdown report (human-readable summary) ---
    md_path = os.path.join(reports_dir, f"{base_name}.md")
    _write_markdown_report(metrics, db_name, md_path, timestamp)

    return json_path


def _write_markdown_report(
    metrics: dict[str, Any],
    db_name: str,
    md_path: str,
    timestamp: str,
) -> None:
    """Write a human-readable Markdown evaluation report.

    Focuses on aggregate metrics and detailed analysis of failed questions only.
    Successful questions are summarized, not listed individually.
    """
    total = metrics["total"]
    valid = metrics["valid_sql"]
    ex_acc = metrics["execution_accuracy"]
    valid_rate = metrics["valid_sql_rate"]
    per_question = metrics["per_question"]

    # --- Partition results ---
    passed = [r for r in per_question if r.get("match")]
    exec_errors = [r for r in per_question if not r.get("executed")]
    wrong_results = [r for r in per_question if r.get("executed") and not r.get("match")]
    pred_failures = [r for r in per_question if r.get("pred_sql") is None]
    gold_failures = [r for r in per_question if r.get("error") and "Gold SQL" in str(r.get("error", ""))]

    lines = [
        f"# Evaluation Report: `{db_name}`",
        "",
        f"**Date:** {timestamp}",
        f"**Total Questions:** {total}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Result | Count | Percentage |",
        f"|--------|-------|------------|",
        f"| ✓ Passed (exec + result match) | {len(passed)} | {len(passed)/total:.1%} |",
        f"| ✗ Wrong result (exec but mismatch) | {len(wrong_results)} | {len(wrong_results)/total:.1%} |",
        f"| E Execution error | {len(exec_errors)} | {len(exec_errors)/total:.1%} |",
        "",
        f"**SQL Generation Time:** "
        f"avg {metrics['avg_elapsed_ms']:.0f} ms | "
        f"min {metrics['min_elapsed_ms']:.0f} ms | "
        f"max {metrics['max_elapsed_ms']:.0f} ms",
        "",
        "---",
        "",
        "## Execution Metrics",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Valid SQL | {valid}/{total} ({valid_rate:.1%}) |",
        f"| Execution Accuracy (EX) | {ex_acc:.1%} |",
        f"| Avg Precision | {metrics['avg_precision']:.3f} ({metrics['avg_precision']:.1%}) |",
        f"| Avg Recall | {metrics['avg_recall']:.3f} ({metrics['avg_recall']:.1%}) |",
        f"| Avg F1 | {metrics['avg_f1']:.3f} |",
        f"| Avg Jaccard | {metrics['avg_jaccard']:.3f} |",
        "",
        "---",
        "",
        "## SQL Component Comparison",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Overall Component F1 | {metrics['avg_component_f1']:.3f} |",
        f"| Avg Name-Level F1 | {metrics['avg_name_f1']:.3f} |",
        "",
        "### Structural Match Rates",
        "",
    ]

    field_rates = metrics.get("component_field_rates", {})
    if field_rates:
        lines.append("| Component | Match Rate |")
        lines.append("|-----------|------------|")
        _field_order = [
            "select_count", "select_distinct", "table_count",
            "join_conds_count",
            "has_where", "where_conditions", "has_group_by", "group_by_count",
            "has_having", "has_order_by", "order_direction", "limit_value",
            "has_union", "has_intersect", "has_except",
        ]
        for field in _field_order:
            if field in field_rates:
                rate = field_rates[field]
                label = field.replace("_", " ").title()
                lines.append(f"| {label} | {rate:.1%} |")
        lines.append("")

    # Name-level F1 averages
    name_avgs = metrics.get("name_field_avgs", {})
    if name_avgs:
        lines.append("### Name-Level F1 Averages")
        lines.append("")
        lines.append("| Component | Avg F1 |")
        lines.append("|-----------|--------|")
        _name_order = ["table_names_f1", "select_cols_f1", "aggregates_f1", "group_by_cols_f1"]
        _name_labels = {
            "table_names_f1": "Table Names",
            "select_cols_f1": "SELECT Columns",
            "aggregates_f1": "Aggregate Functions",
            "group_by_cols_f1": "GROUP BY Columns",
        }
        for field in _name_order:
            if field in name_avgs:
                avg = name_avgs[field]
                label = _name_labels.get(field, field)
                lines.append(f"| {label} | {avg:.3f} |")
        lines.append("")

    lines.extend([
        "",
        "---",
        "",
    ])

    # ── Failed Questions Analysis ──
    failures = exec_errors + wrong_results
    if failures:
        lines.extend([
            "## Failed Questions Analysis",
            "",
            f"**{len(failures)} questions failed** "
            f"({len(exec_errors)} execution errors, {len(wrong_results)} wrong results).",
            "",
        ])

        # -- Execution Errors --
        if exec_errors:
            lines.extend([
                "### Execution Errors",
                "",
                "These SQLs failed to execute against the SQLite database.",
                "",
                "| # | Question | Comp F1 | Error |",
                "|---|----------|---------|-------|",
            ])
            for i, r in enumerate(exec_errors):
                idx = per_question.index(r) + 1
                q = r["question"][:70].replace("|", "\\|")
                comp = r.get("components", {})
                comp_f1 = f"{comp.get('component_f1', 0):.2f}" if comp else "—"
                err = (r.get("error") or "Unknown")[:100].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {idx} | {q} | {comp_f1} | {err} |")

            lines.append("")
            # Show gold vs pred for execution errors
            lines.append("#### Gold vs Predicted (Execution Errors)")
            lines.append("")
            for i, r in enumerate(exec_errors):
                idx = per_question.index(r) + 1
                gold = r.get("gold_sql", "N/A")
                pred = r.get("pred_sql") or "N/A"
                comp = r.get("components", {})
                matched = comp.get("components_matched", 0)
                total_comp = comp.get("components_total", 0)
                # Build a short mismatch summary
                per_field = comp.get("per_field", {})
                mismatches = [k.replace("_", " ") for k, v in per_field.items() if not v]
                mm_summary = ", ".join(mismatches[:5]) if mismatches else "none"
                lines.extend([
                    f"**Q{idx}:** {r['question'][:100]}",
                    f"  Comp F1: {comp.get('component_f1', 0):.2f} ({matched}/{total_comp} matched)",
                    f"  Mismatches: {mm_summary}",
                    "",
                    "```sql",
                    f"-- GOLD: {gold}",
                    f"-- PRED: {pred}",
                    "```",
                    "",
                ])

        # -- Wrong Results --
        if wrong_results:
            lines.extend([
                "### Wrong Results (Executed but Mismatched)",
                "",
                "These SQLs executed successfully but returned different results from gold.",
                "",
                "| # | Question | Comp F1 | Result P / R / F1 |",
                "|---|----------|---------|-------------------|",
            ])
            for i, r in enumerate(wrong_results):
                idx = per_question.index(r) + 1
                q = r["question"][:70].replace("|", "\\|")
                comp = r.get("components", {})
                comp_f1 = f"{comp.get('component_f1', 0):.2f}" if comp else "—"
                inter = r.get("intersection", {})
                res_str = (
                    f"{inter.get('precision', 0):.2f} / "
                    f"{inter.get('recall', 0):.2f} / "
                    f"{inter.get('f1', 0):.2f}"
                )
                lines.append(f"| {idx} | {q} | {comp_f1} | {res_str} |")

            lines.append("")
            # Show gold vs pred for wrong results
            lines.append("#### Gold vs Predicted (Wrong Results)")
            lines.append("")
            for i, r in enumerate(wrong_results):
                idx = per_question.index(r) + 1
                gold = r.get("gold_sql", "N/A")
                pred = r.get("pred_sql") or "N/A"
                comp = r.get("components", {})
                inter = r.get("intersection", {})
                matched = comp.get("components_matched", 0)
                total_comp = comp.get("components_total", 0)
                per_field = comp.get("per_field", {})
                mismatches = [k.replace("_", " ") for k, v in per_field.items() if not v]
                mm_summary = ", ".join(mismatches[:5]) if mismatches else "none"
                lines.extend([
                    f"**Q{idx}:** {r['question'][:100]}",
                    f"  Comp F1: {comp.get('component_f1', 0):.2f} ({matched}/{total_comp} matched)"
                    f" | Result F1: {inter.get('f1', 0):.2f}",
                    f"  Mismatches: {mm_summary}",
                    "",
                    "```sql",
                    f"-- GOLD: {gold}",
                    f"-- PRED: {pred}",
                    "```",
                    "",
                ])
    else:
        lines.extend([
            "## All Questions Passed ✓",
            "",
            "No failures to analyze.",
            "",
        ])

    # ── Failure Pattern Analysis ──
    if failures:
        lines.extend([
            "---",
            "",
            "## Failure Pattern Analysis",
            "",
        ])

        # Component F1 distribution for failures
        fail_comp_f1s = [
            r.get("components", {}).get("component_f1", 0)
            for r in failures if r.get("components")
        ]
        if fail_comp_f1s:
            avg_fail_comp = sum(fail_comp_f1s) / len(fail_comp_f1s)
            lines.extend([
                f"- **Avg Component F1 on failures:** {avg_fail_comp:.3f}",
                f"- **Low Component F1 (<0.5):** {sum(1 for f in fail_comp_f1s if f < 0.5)}/{len(fail_comp_f1s)}",
                f"- **High Component F1 (≥0.7):** {sum(1 for f in fail_comp_f1s if f >= 0.7)}/{len(fail_comp_f1s)}",
                "",
            ])

        # Most common mismatched components across failures
        all_mismatches = []
        for r in failures:
            per_field = r.get("components", {}).get("per_field", {})
            for field, matched in per_field.items():
                if not matched:
                    all_mismatches.append(field)
        if all_mismatches:
            from collections import Counter
            mm_counts = Counter(all_mismatches)
            lines.append("### Most Mismatched Components")
            lines.append("")
            lines.append("| Component | Fail Count |")
            lines.append("|-----------|------------|")
            for field, count in mm_counts.most_common(5):
                lines.append(f"| {field.replace('_', ' ').title()} | {count}/{len(failures)} |")
            lines.append("")

        # Common error patterns
        error_messages = [r.get("error", "") for r in failures if r.get("error")]
        no_such_table = sum(1 for e in error_messages if "no such table" in e.lower())
        no_such_column = sum(1 for e in error_messages if "no such column" in e.lower())
        syntax_err = sum(1 for e in error_messages if "syntax" in e.lower() or "near" in e.lower())

        if no_such_table or no_such_column or syntax_err:
            lines.append("### Common Error Types")
            lines.append("")
            lines.append(f"| Error Type | Count |")
            lines.append(f"|------------|-------|")
            if no_such_table:
                lines.append(f"| No such table | {no_such_table} |")
            if no_such_column:
                lines.append(f"| No such column | {no_such_column} |")
            if syntax_err:
                lines.append(f"| SQL syntax error | {syntax_err} |")
            lines.append("")

        # Interpretation guidance
        lines.extend([
            "### Interpretation",
            "",
            "- **Low Component F1 + execution error** → SQL structure is very different from gold (wrong tables, missing WHERE, etc.).",
            "- **High Component F1 + execution error** → SQL structure is close but uses wrong table/column names or unsupported syntax.",
            "- **High Component F1 + wrong result** → SQL structure matches but logic details (filter values, join conditions) differ.",
            "- **Low Component F1 + wrong result** → SQL structure is semantically different; the model may have misunderstood the question.",
            "",
        ])

    with open(md_path, "w") as f:
        f.write("\n".join(lines))


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate text-to-SQL on Spider dataset"
    )
    parser.add_argument(
        "--db",
        type=str,
        required=True,
        help="Spider database name (e.g., department_store)",
    )
    parser.add_argument(
        "--test-file",
        type=str,
        default=None,
        help="Path to test JSON file (default: spider_dir/department_store_enhanced_test.json)",
    )
    parser.add_argument(
        "--spider-dir",
        type=str,
        default=SPIDER_DATA_DIR,
        help="Path to Spider dataset directory",
    )
    parser.add_argument(
        "--descriptor",
        type=str,
        default=None,
        help="Path to descriptor config",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of test questions",
    )

    args = parser.parse_args()
    asyncio.run(
        evaluate_spider_db(
            db_name=args.db,
            test_file=args.test_file,
            spider_dir=args.spider_dir,
            descriptor_path=args.descriptor,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()