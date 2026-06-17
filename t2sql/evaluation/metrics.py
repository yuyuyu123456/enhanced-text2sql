"""Evaluation metrics for text-to-SQL systems.

Supported metrics:
- Execution Accuracy (EX): Exact match of result sets (order-independent)
- Valid SQL Rate: Fraction of generated SQLs that execute without error
- Intersection Metrics: Precision / Recall / F1 / Jaccard on result set overlap
  (soft metrics — measure partial correctness when exact match fails)
- SQL Component Comparison: Clause-level structural comparison between
  generated SQL and the gold Spider-format sql structure
"""

import re
import pandas as pd
from typing import Any
from t2sql.utils import logger

# ── SQL clause regexes for parsing raw SQL strings ──

_SELECT_RE = re.compile(r'\bSELECT\s+(DISTINCT\s+)?(.+?)\s*\bFROM\b', re.IGNORECASE | re.DOTALL)
_FROM_TABLE_RE = re.compile(
    r'(?:\bFROM\b|\bJOIN\b|\bINNER\s+JOIN\b|\bLEFT\s+(?:OUTER\s+)?JOIN\b'
    r'|\bRIGHT\s+(?:OUTER\s+)?JOIN\b|\bFULL\s+(?:OUTER\s+)?JOIN\b'
    r'|\bCROSS\s+JOIN\b)'
    r'\s+(\w+)',
    re.IGNORECASE,
)
_WHERE_RE = re.compile(r'\bWHERE\b\s+(.+?)(?:\bGROUP\b|\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', re.IGNORECASE | re.DOTALL)
_GROUP_BY_RE = re.compile(r'\bGROUP\s+BY\b\s+(.+?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', re.IGNORECASE | re.DOTALL)
_ORDER_BY_RE = re.compile(r'\bORDER\s+BY\b\s+(.+?)(?:\bLIMIT\b|$)', re.IGNORECASE | re.DOTALL)
_LIMIT_RE = re.compile(r'\bLIMIT\b\s+(\d+)', re.IGNORECASE)
_AGG_RE = re.compile(r'\b(COUNT|SUM|AVG|MAX|MIN)\s*\(', re.IGNORECASE)
_JOIN_RE = re.compile(
    r'\b(?:INNER\s+JOIN|LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN'
    r'|FULL\s+(?:OUTER\s+)?JOIN|CROSS\s+JOIN|JOIN)\b',
    re.IGNORECASE,
)
_AND_OR_RE = re.compile(r'\b(AND|OR)\b', re.IGNORECASE)

# For extracting bare column names from SELECT expressions
_COL_NAME_RE = re.compile(
    r'(?:(?:\w+)\.)?(\w+)',  # strip table alias prefix
)
# For extracting table names from FROM/JOIN clauses (strip alias, keep real name)
_TABLE_NAME_RE = re.compile(
    r'(?:\bFROM\b|\bJOIN\b|\bINNER\s+JOIN\b|\bLEFT\s+(?:OUTER\s+)?JOIN\b'
    r'|\bRIGHT\s+(?:OUTER\s+)?JOIN\b|\bFULL\s+(?:OUTER\s+)?JOIN\b'
    r'|\bCROSS\s+JOIN\b)'
    r'\s+(\w+)(?:\s+(?:AS\s+)?\w+)?',
    re.IGNORECASE,
)


def _extract_gold_components(item_sql: dict[str, Any]) -> dict[str, Any]:
    """Extract structural components from Spider-format gold `sql` dict.

    Args:
        item_sql: The ``item["sql"]`` dict from a Spider test entry.

    Returns:
        Flat dict of component counts and flags.
    """
    select_clause = item_sql.get("select", [False, []])
    from_clause = item_sql.get("from", {})
    where_clause = item_sql.get("where", [])
    group_by = item_sql.get("groupBy", [])
    having = item_sql.get("having", [])
    order_by = item_sql.get("orderBy", [])

    return {
        "select_count": len(select_clause[1]) if len(select_clause) > 1 else 0,
        "select_distinct": select_clause[0] is True if select_clause else False,
        "table_count": len(from_clause.get("table_units", [])),
        "join_conds_count": len(from_clause.get("conds", [])),
        "has_where": len(where_clause) > 0,
        "where_conditions": len(where_clause),
        "has_group_by": len(group_by) > 0,
        "group_by_count": len(group_by),
        "has_having": len(having) > 0,
        "has_order_by": len(order_by) > 0,
        "order_direction": str(order_by[0]) if order_by else None,
        "limit_value": item_sql.get("limit"),
        "has_union": item_sql.get("union") is not None,
        "has_intersect": item_sql.get("intersect") is not None,
        "has_except": item_sql.get("except") is not None,
    }


def _parse_sql_full(sql: str) -> dict[str, Any]:
    """Parse a raw SQL string into structural counts AND extracted names.

    Args:
        sql: Raw SQL string (gold or predicted).

    Returns:
        Flat dict of component counts, flags, table names, column names, and
        aggregate functions.
    """
    if not sql or not sql.strip():
        return _empty_components()

    # Inline comments / chain-of-thought: strip everything before the actual SQL
    select_pos = re.search(r'\bSELECT\b', sql, re.IGNORECASE)
    if select_pos:
        sql_body = sql[select_pos.start():]
    else:
        sql_body = sql

    sql_upper = sql_body.upper()

    # ── SELECT columns ──
    select_cols_raw = []
    select_m = _SELECT_RE.search(sql_body)
    if select_m:
        distinct = select_m.group(1) is not None
        cols_raw = select_m.group(2)
        cols = _split_on_commas(cols_raw)
        select_count = len(cols)
        # Extract bare column names (strip table alias like "T1.staff_name" → "staff_name")
        for col in cols:
            col_stripped = col.strip()
            # For aggregate expressions, grab the inner column
            inner_m = re.search(r'\(\s*((?:\w+\.)?\w+)', col_stripped)
            if inner_m:
                col_stripped = inner_m.group(1)
            # Strip table alias prefix (T1.xxx → xxx)
            dot_m = re.search(r'(?:\w+)\.(\w+)', col_stripped)
            if dot_m:
                select_cols_raw.append(dot_m.group(1).lower())
            else:
                # Just the bare name
                bare_m = re.search(r'(\w+)', col_stripped)
                if bare_m:
                    select_cols_raw.append(bare_m.group(1).lower())
    else:
        distinct = False
        select_count = 0

    # ── Aggregates in SELECT ──
    agg_names = [a.lower() for a in _AGG_RE.findall(sql_body)]

    # ── FROM tables (raw names, lowercased) ──
    table_names = list(dict.fromkeys(  # preserve order, dedupe
        t.lower() for t in _FROM_TABLE_RE.findall(sql_body)
    ))
    table_count = len(table_names)

    # ── JOIN count ──
    joins = _JOIN_RE.findall(sql_body)
    join_count = len(joins)

    # ── WHERE ──
    where_m = _WHERE_RE.search(sql_body)
    has_where = where_m is not None
    where_conditions = 0
    if has_where and where_m:
        where_text = where_m.group(1)
        where_conditions = len(_AND_OR_RE.findall(where_text)) + 1

    # ── GROUP BY ──
    group_m = _GROUP_BY_RE.search(sql_body)
    has_group_by = group_m is not None
    group_by_cols_raw = []
    group_by_count = 0
    if has_group_by and group_m:
        group_cols = _split_on_commas(group_m.group(1))
        group_by_count = len(group_cols)
        for col in group_cols:
            col = col.strip().lower()
            dot_m = re.search(r'(?:\w+)\.(\w+)', col)
            if dot_m:
                group_by_cols_raw.append(dot_m.group(1))
            else:
                bare_m = re.search(r'(\w+)', col)
                if bare_m:
                    group_by_cols_raw.append(bare_m.group(1))

    # ── HAVING ──
    has_having = bool(re.search(r'\bHAVING\b', sql_body, re.IGNORECASE))

    # ── ORDER BY ──
    order_m = _ORDER_BY_RE.search(sql_body)
    has_order_by = order_m is not None
    order_direction = None
    if has_order_by and order_m:
        order_text = order_m.group(1).upper()
        has_desc = 'DESC' in order_text
        has_asc = 'ASC' in order_text
        if has_desc and not has_asc:
            order_direction = 'desc'
        elif has_asc and not has_desc:
            order_direction = 'asc'
        elif has_desc and has_asc:
            order_direction = 'mixed'
        else:
            order_direction = 'asc'

    # ── LIMIT ──
    limit_m = _LIMIT_RE.search(sql_body)
    limit_value = int(limit_m.group(1)) if limit_m else None

    # ── Set operations ──
    has_union = bool(re.search(r'\bUNION\b', sql_upper))
    has_intersect = bool(re.search(r'\bINTERSECT\b', sql_upper))
    has_except = bool(re.search(r'\bEXCEPT\b', sql_upper))

    return {
        # Structural counts
        "select_count": select_count,
        "select_distinct": distinct,
        "table_count": table_count,
        "join_conds_count": join_count,
        "has_where": has_where,
        "where_conditions": where_conditions,
        "has_group_by": has_group_by,
        "group_by_count": group_by_count,
        "has_having": has_having,
        "has_order_by": has_order_by,
        "order_direction": order_direction,
        "limit_value": limit_value,
        "has_union": has_union,
        "has_intersect": has_intersect,
        "has_except": has_except,
        # Actual names for content-level comparison
        "table_names": table_names,
        "select_col_names": select_cols_raw,
        "aggregate_funcs": agg_names,
        "group_by_col_names": group_by_cols_raw,
    }


def _split_on_commas(text: str) -> list[str]:
    """Split on commas not inside parentheses."""
    parts = []
    depth = 0
    current = ""
    for ch in text:
        if ch == ',' and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _empty_components() -> dict[str, Any]:
    """Return an empty component dict (all zeros / falses / empty lists)."""
    return {
        "select_count": 0,
        "select_distinct": False,
        "table_count": 0,
        "join_conds_count": 0,
        "has_where": False,
        "where_conditions": 0,
        "has_group_by": False,
        "group_by_count": 0,
        "has_having": False,
        "has_order_by": False,
        "order_direction": None,
        "limit_value": None,
        "has_union": False,
        "has_intersect": False,
        "has_except": False,
        "table_names": [],
        "select_col_names": [],
        "aggregate_funcs": [],
        "group_by_col_names": [],
    }


def _set_f1(pred_set: set, gold_set: set) -> dict[str, float]:
    """Compute precision, recall, and F1 for two sets."""
    if not pred_set and not gold_set:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    inter = pred_set & gold_set
    precision = len(inter) / len(pred_set) if pred_set else 0.0
    recall = len(inter) / len(gold_set) if gold_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def compare_sql_components(
    pred_sql: str | None,
    gold_item_sql: dict[str, Any],
    gold_sql_str: str = "",
) -> dict[str, Any]:
    """Compare predicted SQL against the gold Spider-format ``sql`` structure.

    Compares at two levels:

    1. **Structural** (from ``item['sql']`` and parsed ``pred_sql``):
       SELECT count, FROM table count, WHERE/HAVING/ORDER BY/LIMIT presence,
       set operations — are the clause types and counts matching?

    2. **Content / names** (parsed from both SQL strings):
       Table name overlap, SELECT column name overlap, aggregate function overlap.

    Args:
        pred_sql: Generated raw SQL string (may be None if generation failed).
        gold_item_sql: The ``item["sql"]`` dict from the Spider test entry.
        gold_sql_str: The gold ``item["query"]`` SQL string, used to extract
                      actual table/column/aggregate names for content comparison.

    Returns:
        Dict with per-component match booleans, name-level F1 scores,
        component score, and overall normalized component F1.
    """
    gold_struct = _extract_gold_components(gold_item_sql)
    gold_names = _parse_sql_full(gold_sql_str) if gold_sql_str else _empty_components()

    if not pred_sql:
        return {
            **{f"gold_{k}": v for k, v in gold_struct.items()},
            "pred_select_count": 0,
            "pred_table_count": 0,
            "pred_has_where": False,
            "pred_has_group_by": False,
            "pred_has_order_by": False,
            "pred_has_having": False,
            "pred_limit_value": None,
            "pred_has_union": False,
            "pred_has_intersect": False,
            "pred_has_except": False,
            "pred_table_names": [],
            "pred_select_col_names": [],
            "pred_aggregate_funcs": [],
            "components_matched": 0,
            "components_total": 0,
            "component_f1": 0.0,
            "per_field": {},
        }

    pred = _parse_sql_full(pred_sql)

    # ═══════════════════════════════════════════
    # 1. Structural comparison (counts / presence)
    # ═══════════════════════════════════════════
    per_field = {}

    per_field["select_count"] = pred["select_count"] == gold_struct["select_count"]
    per_field["select_distinct"] = pred["select_distinct"] == gold_struct["select_distinct"]
    per_field["table_count"] = pred["table_count"] == gold_struct["table_count"]
    per_field["join_conds_count"] = pred["join_conds_count"] == gold_struct["join_conds_count"]
    per_field["has_where"] = pred["has_where"] == gold_struct["has_where"]
    per_field["where_conditions"] = abs(pred["where_conditions"] - gold_struct["where_conditions"]) <= 1
    per_field["has_group_by"] = pred["has_group_by"] == gold_struct["has_group_by"]
    per_field["group_by_count"] = pred["group_by_count"] == gold_struct["group_by_count"]
    per_field["has_having"] = pred["has_having"] == gold_struct["has_having"]
    per_field["has_order_by"] = pred["has_order_by"] == gold_struct["has_order_by"]
    per_field["order_direction"] = pred["order_direction"] == gold_struct["order_direction"]
    per_field["limit_value"] = pred["limit_value"] == gold_struct["limit_value"]
    per_field["has_union"] = pred["has_union"] == gold_struct["has_union"]
    per_field["has_intersect"] = pred["has_intersect"] == gold_struct["has_intersect"]
    per_field["has_except"] = pred["has_except"] == gold_struct["has_except"]

    # ═══════════════════════════════════════════
    # 2. Content / name comparison (set overlap)
    # ═══════════════════════════════════════════
    pred_tables = set(pred["table_names"])
    gold_tables = set(gold_names["table_names"])
    table_f1 = _set_f1(pred_tables, gold_tables)
    per_field["table_names_f1"] = table_f1["f1"]

    pred_cols = set(pred["select_col_names"])
    gold_cols = set(gold_names["select_col_names"])
    col_f1 = _set_f1(pred_cols, gold_cols)
    per_field["select_cols_f1"] = col_f1["f1"]

    pred_aggs = set(pred["aggregate_funcs"])
    gold_aggs = set(gold_names["aggregate_funcs"])
    agg_f1 = _set_f1(pred_aggs, gold_aggs)
    per_field["aggregates_f1"] = agg_f1["f1"]

    pred_gb = set(pred["group_by_col_names"])
    gold_gb = set(gold_names["group_by_col_names"])
    gb_f1 = _set_f1(pred_gb, gold_gb)
    per_field["group_by_cols_f1"] = gb_f1["f1"]

    # ═══════════════════════════════════════════
    # 3. Aggregate scores
    # ═══════════════════════════════════════════
    # Structural matches (boolean → 0/1)
    structural_matched = sum(
        1 for k, v in per_field.items()
        if k not in ("table_names_f1", "select_cols_f1", "aggregates_f1", "group_by_cols_f1") and v
    )
    structural_total = sum(
        1 for k in per_field
        if k not in ("table_names_f1", "select_cols_f1", "aggregates_f1", "group_by_cols_f1")
    )

    # Name-level F1s averaged
    name_f1s = [
        per_field.get("table_names_f1", 0),
        per_field.get("select_cols_f1", 0),
        per_field.get("aggregates_f1", 0),
        per_field.get("group_by_cols_f1", 0),
    ]
    avg_name_f1 = sum(name_f1s) / len(name_f1s) if name_f1s else 0.0

    # Overall: weight structural 40%, names 60% (names carry more signal)
    overall_f1 = 0.4 * (structural_matched / structural_total) + 0.6 * avg_name_f1 \
        if structural_total > 0 else 0.0

    return {
        **{f"gold_{k}": v for k, v in gold_struct.items()},
        **{f"gold_{k}": v for k, v in gold_names.items()
           if k in ("table_names", "select_col_names", "aggregate_funcs", "group_by_col_names")},
        **{f"pred_{k}": v for k, v in pred.items()},
        "components_matched": structural_matched,
        "components_total": structural_total,
        "avg_name_f1": avg_name_f1,
        "component_f1": overall_f1,
        "per_field": per_field,
    }


def _result_set_to_tuples(df: pd.DataFrame) -> set[tuple]:
    """Convert a DataFrame result set into a set of tuples for comparison."""
    return set(tuple(row) for row in df.itertuples(index=False))


def compare_result_sets(
    df_pred: pd.DataFrame, df_gold: pd.DataFrame
) -> bool:
    """Compare two result DataFrames for equality (order-independent).

    Args:
        df_pred: Result from predicted SQL.
        df_gold: Result from gold/reference SQL.

    Returns:
        True if result sets are equivalent.
    """
    if df_pred.shape != df_gold.shape:
        return False

    # Sort both by all columns for order-independent comparison
    cols = list(df_pred.columns)
    try:
        pred_sorted = df_pred.sort_values(by=cols).reset_index(drop=True)
        gold_sorted = df_gold.sort_values(by=cols).reset_index(drop=True)
        return pred_sorted.equals(gold_sorted)
    except Exception:
        return _result_set_to_tuples(df_pred) == _result_set_to_tuples(df_gold)


def compare_result_sets_intersection(
    df_pred: pd.DataFrame, df_gold: pd.DataFrame
) -> dict[str, float]:
    """Compute soft intersection metrics between pred and gold result sets.

    Unlike exact match, this measures partial overlap — useful when the
    generated SQL returns mostly-correct but not identical results.

    Metrics:
        precision: |pred ∩ gold| / |pred|  — how many pred rows are correct
        recall:    |pred ∩ gold| / |gold|  — how many gold rows were found
        f1:        2 * P * R / (P + R)     — harmonic mean
        jaccard:   |pred ∩ gold| / |pred ∪ gold|  — IoU

    Args:
        df_pred: Result from predicted SQL.
        df_gold: Result from gold/reference SQL.

    Returns:
        Dict with precision, recall, f1, jaccard (all 0.0–1.0).
    """
    pred_set = _result_set_to_tuples(df_pred)
    gold_set = _result_set_to_tuples(df_gold)

    if len(pred_set) == 0 and len(gold_set) == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "jaccard": 1.0}

    intersection = pred_set & gold_set
    union = pred_set | gold_set

    precision = len(intersection) / len(pred_set) if len(pred_set) > 0 else 0.0
    recall = len(intersection) / len(gold_set) if len(gold_set) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    jaccard = len(intersection) / len(union) if len(union) > 0 else 0.0

    return {"precision": precision, "recall": recall, "f1": f1, "jaccard": jaccard}


def compute_metrics(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate evaluation metrics from per-question results.

    Args:
        results: List of per-question result dicts, each containing:
            - question: str
            - gold_sql: str
            - pred_sql: str or None
            - executed: bool (whether pred SQL ran successfully)
            - match: bool (whether result sets match exactly)
            - intersection: dict (precision/recall/f1/jaccard) if executed
            - components: dict (component comparison metrics) if pred_sql exists

    Returns:
        Dict with aggregate metrics:
            - total: Total questions evaluated
            - valid_sql: Number of valid/executable SQLs
            - avg_elapsed_ms / min_elapsed_ms / max_elapsed_ms: SQL generation timing
            - ...
            - valid_sql_rate: Fraction of valid SQLs
            - execution_accuracy: Fraction with exactly matching result sets
            - avg_component_f1: Mean per-component match rate
            - component_field_rates: Dict mapping field name → match rate
            - avg_precision / avg_recall / avg_f1 / avg_jaccard:
              Mean intersection metrics over all executed queries
            - per_question: List of per-question results
    """
    total = len(results)
    valid = sum(1 for r in results if r.get("executed", False))
    matched = sum(1 for r in results if r.get("match", False))

    # Aggregate intersection metrics over executed queries
    executed_results = [r for r in results if r.get("executed", False)]
    avg_precision = (
        sum(r["intersection"]["precision"] for r in executed_results) / len(executed_results)
        if executed_results else 0.0
    )
    avg_recall = (
        sum(r["intersection"]["recall"] for r in executed_results) / len(executed_results)
        if executed_results else 0.0
    )
    avg_f1 = (
        sum(r["intersection"]["f1"] for r in executed_results) / len(executed_results)
        if executed_results else 0.0
    )
    avg_jaccard = (
        sum(r["intersection"]["jaccard"] for r in executed_results) / len(executed_results)
        if executed_results else 0.0
    )

    # Aggregate SQL component comparison metrics
    results_with_components = [r for r in results if r.get("components")]
    n_comp = len(results_with_components)

    avg_component_f1 = (
        sum(r["components"]["component_f1"] for r in results_with_components) / n_comp
        if n_comp else 0.0
    )

    avg_name_f1 = (
        sum(r["components"].get("avg_name_f1", 0) for r in results_with_components) / n_comp
        if n_comp else 0.0
    )

    # Per-field match rates (structural: boolean fields)
    field_rates = {}
    # Continuous name-level F1 averages
    name_field_avgs = {}
    if n_comp > 0:
        first_per_field = results_with_components[0]["components"]["per_field"]
        for field in first_per_field:
            values = [
                r["components"]["per_field"].get(field, 0)
                for r in results_with_components
            ]
            if all(isinstance(v, bool) for v in values):
                # Boolean structural field: report as match rate
                field_rates[field] = sum(1 for v in values if v) / n_comp
            else:
                # Continuous name-level F1: report as average
                name_field_avgs[field] = sum(values) / n_comp

    # Timing aggregation
    elapsed_times = [r.get("elapsed_ms", 0) for r in results if r.get("elapsed_ms")]
    avg_elapsed = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0.0
    min_elapsed = min(elapsed_times) if elapsed_times else 0.0
    max_elapsed = max(elapsed_times) if elapsed_times else 0.0

    return {
        "total": total,
        "valid_sql": valid,
        "valid_sql_rate": valid / total if total > 0 else 0.0,
        "execution_accuracy": matched / total if total > 0 else 0.0,
        "avg_component_f1": avg_component_f1,
        "avg_name_f1": avg_name_f1,
        "component_field_rates": field_rates,
        "name_field_avgs": name_field_avgs,
        "avg_elapsed_ms": avg_elapsed,
        "min_elapsed_ms": min_elapsed,
        "max_elapsed_ms": max_elapsed,
        "avg_precision": avg_precision,
        "avg_recall": avg_recall,
        "avg_f1": avg_f1,
        "avg_jaccard": avg_jaccard,
        "per_question": results,
    }


def print_evaluation_report(metrics: dict[str, Any]) -> None:
    """Print a formatted evaluation report."""
    field_rates = metrics.get("component_field_rates", {})
    name_avgs = metrics.get("name_field_avgs", {})

    print("\n" + "=" * 60)
    print("  EVALUATION REPORT")
    print("=" * 60)
    print(f"  Total questions:        {metrics['total']}")
    print(f"  Valid SQL:              {metrics['valid_sql']}/{metrics['total']} "
          f"({metrics['valid_sql_rate']:.1%})")
    print(f"  Execution Accuracy:     {metrics['execution_accuracy']:.1%}")
    print(f"  SQL Generation Time:")
    print(f"    Avg:  {metrics['avg_elapsed_ms']:.0f} ms")
    print(f"    Min:  {metrics['min_elapsed_ms']:.0f} ms")
    print(f"    Max:  {metrics['max_elapsed_ms']:.0f} ms")
    print("-" * 60)
    print(f"  SQL Component Comparison:")
    print(f"    Overall Component F1: {metrics['avg_component_f1']:.3f}")
    print(f"    Avg Name-Level F1:    {metrics['avg_name_f1']:.3f}")
    if field_rates:
        print(f"    Structural match rates:")
        for field, rate in sorted(field_rates.items()):
            print(f"      {field:<22s} {rate:.1%}")
    if name_avgs:
        print(f"    Name-level F1 averages:")
        for field, avg in sorted(name_avgs.items()):
            print(f"      {field:<22s} {avg:.3f}")
    print("-" * 60)
    print(f"  Result Set Intersection Metrics (avg over executed queries):")
    print(f"    Precision:  {metrics['avg_precision']:.3f}  "
          f"({metrics['avg_precision']:.1%})")
    print(f"    Recall:     {metrics['avg_recall']:.3f}  "
          f"({metrics['avg_recall']:.1%})")
    print(f"    F1:         {metrics['avg_f1']:.3f}")
    print(f"    Jaccard:    {metrics['avg_jaccard']:.3f}")
    print("=" * 60)

    # Show per-question details
    print("\n  Per-Question Results:")
    print("-" * 60)
    for i, r in enumerate(metrics["per_question"]):
        status = "✓" if r.get("match") else "✗"
        if not r.get("executed"):
            status = "E"  # Error
        line = f"  [{status}] {r['question'][:70]}..."
        # Show elapsed time
        elapsed = r.get("elapsed_ms")
        if elapsed:
            line += f"  {elapsed:.0f}ms"
        # Show component F1 for all predicted queries
        comp = r.get("components", {})
        if comp:
            line += f"  compF1={comp.get('component_f1', 0):.2f}"
        # Show intersection metrics for non-matching but executed queries
        if r.get("executed") and not r.get("match"):
            inter = r.get("intersection", {})
            line += (
                f"  resP={inter.get('precision', 0):.2f} "
                f"R={inter.get('recall', 0):.2f} "
                f"F1={inter.get('f1', 0):.2f}"
            )
        print(line)
        if r.get("error"):
            print(f"        Error: {str(r['error'])[:80]}")
    print("-" * 60)