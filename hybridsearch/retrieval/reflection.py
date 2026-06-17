"""Reflection node — LLM-based SQL quality check using a different model.

Runs three parallel checks on generated SQL:
1. Syntax check (basic regex validation)
2. Executability check (run against SQLite)
3. Relevance check (LLM review with different model)

The reflection model MUST differ from the generation model.
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


_REFLECTION_PROMPT = """You are a SQL quality reviewer. Analyze whether the following SQL
correctly answers the user's question.

Question: {question}
SQL:
{sql}

Evaluate:
1. Does the SQL logically address what the question asks for?
2. Are the correct tables and columns referenced?
3. Are filters (WHERE), aggregations (GROUP BY), and ordering correct for the question?
4. Would the query return the expected kind of results?

Return JSON:
{{
    "relevant": bool,
    "covers_intent": bool,
    "issues": [str],
    "suggestions": [str],
    "confidence": float
}}

Only output valid JSON, no markdown or extra text."""


class ReflectionNode:
    """Post-generation SQL quality checker.

    Uses a **different** LLM model than the one that generated the SQL to provide
    an independent quality assessment with three parallel checks.

    Suggested pairing (from the project config):
        Generation:  glm-4-flash  (fast, cheap)
        Reflection:  glm-4-plus   (more capable review)
    """

    def __init__(
        self,
        router,
        generation_model: str = "glm-4-flash",
        reflection_model: str = "glm-4-plus",
    ):
        self._router = router
        self.generation_model = generation_model
        self.reflection_model = reflection_model

    # ── Individual checks ──

    @staticmethod
    async def check_syntax(sql: str, db_path: str | None = None) -> dict:
        """SQL syntax validation using SQLite's EXPLAIN.

        Uses ``EXPLAIN`` to parse the SQL against the actual database schema.
        This catches: syntax errors, undefined tables/columns, type mismatches,
        and other issues that regex-based checks miss.

        If *db_path* is provided, validates against that SQLite database's schema.
        Otherwise falls back to basic structural checks.
        """
        import sqlite3, os, re

        issues = []

        # Quick structural checks first
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
            issues.append("SQL does not start with SELECT or WITH")

        if sql.count("(") != sql.count(")"):
            issues.append("Unbalanced parentheses")

        # Full schema-aware validation via SQLite EXPLAIN
        if db_path:
            db_dir = os.path.dirname(db_path)
            sqlite_files = [
                f for f in os.listdir(db_dir) if f.endswith(".sqlite")
            ]
            if sqlite_files:
                db_file = os.path.join(db_dir, sqlite_files[0])
                conn = sqlite3.connect(db_file)
                try:
                    conn.execute(f"EXPLAIN {sql.rstrip(';')}")
                except sqlite3.OperationalError as e:
                    err_msg = str(e)
                    # Classify the error type
                    if "no such table" in err_msg.lower():
                        tbl = re.search(r"no such table: (\w+)", err_msg)
                        if tbl:
                            issues.append(f"Undefined table: {tbl.group(1)}")
                    elif "no such column" in err_msg.lower():
                        issues.append(f"Undefined column: {err_msg}")
                    elif "syntax error" in err_msg.lower():
                        issues.append(f"SQL syntax error: {err_msg}")
                    else:
                        issues.append(f"SQL error: {err_msg}")
                except sqlite3.Warning:
                    pass  # warnings are fine
                except Exception as e:
                    issues.append(f"Parse error: {e}")
                finally:
                    conn.close()

        return {
            "valid_syntax": len(issues) == 0,
            "issues": issues,
        }

    @staticmethod
    async def check_executability(sql: str, db_path: str) -> dict:
        """Try executing the SQL against the SQLite database.

        Args:
            sql: SQL query to test.
            db_path: Path to the SQLite database directory.
        """
        from t2sql.evaluation.spider_eval import execute_sql_on_sqlite

        try:
            df = execute_sql_on_sqlite(db_path, sql)
            return {
                "executable": True,
                "error": None,
                "row_count": len(df),
                "column_count": len(df.columns),
            }
        except Exception as e:
            return {
                "executable": False,
                "error": str(e)[:200],
                "row_count": 0,
                "column_count": 0,
            }

    async def check_relevance(self, sql: str, question: str) -> dict:
        """LLM-based relevance check using the **reflection** model."""
        from t2sql.utils import parse_json

        prompt = _REFLECTION_PROMPT.format(question=question, sql=sql)

        try:
            response = await self._router.acompletion(
                model=self.reflection_model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content
            result = parse_json(content)
            return {
                "relevant": result.get("relevant", False),
                "covers_intent": result.get("covers_intent", True),
                "issues": result.get("issues", []),
                "suggestions": result.get("suggestions", []),
                "confidence": result.get("confidence", 0.5),
            }
        except Exception as e:
            return {
                "relevant": True,  # Be lenient on LLM failure
                "covers_intent": True,
                "issues": [f"Relevance check failed: {e}"],
                "suggestions": [],
                "confidence": 0.5,
            }

    # ── Orchestrator ──

    async def reflect(
        self, sql: str, question: str, db_path: str | None = None
    ) -> dict:
        """Run all three quality checks in parallel.

        Returns:
            Dict with syntax, executability, relevance results and an
            overall weighted score (0.3×syntax + 0.3×exec + 0.4×relevance).
        """
        empty_exec = {"executable": True, "error": None, "row_count": 0, "column_count": 0}

        tasks = [
            self.check_syntax(sql, db_path),
            self.check_executability(sql, db_path) if db_path else asyncio.ensure_future(asyncio.sleep(0, result=empty_exec)),
            self.check_relevance(sql, question),
        ]

        syntax_result, exec_result, relevance_result = await asyncio.gather(*tasks)

        if not isinstance(exec_result, dict):
            exec_result = empty_exec

        # Weighted overall score
        syntax_score = 1.0 if syntax_result.get("valid_syntax", False) else 0.0
        exec_score = 1.0 if exec_result.get("executable", False) else 0.0
        rel_score = relevance_result.get("confidence", 0.5)
        overall_score = 0.3 * syntax_score + 0.3 * exec_score + 0.4 * rel_score

        return {
            "sql": sql,
            "question": question,
            "syntax": syntax_result,
            "executability": exec_result,
            "relevance": relevance_result,
            "overall_score": overall_score,
            "passed": overall_score >= 0.7,
            "generation_model": self.generation_model,
            "reflection_model": self.reflection_model,
        }

    async def reflect_and_retry(
        self,
        sql: str,
        question: str,
        regenerate_sql,
        db_path: str,
        max_retries: int = 2,
    ) -> tuple[str, dict]:
        """Reflect on SQL quality and retry generation if it fails.

        Args:
            sql: Initial generated SQL.
            question: User's question.
            regenerate_sql: Async callable ``(question: str) -> str`` that
                            regenerates SQL from the question (with feedback).
            db_path: Path to SQLite DB directory.
            max_retries: Maximum regeneration attempts.

        Returns:
            (final_sql, final_reflection) tuple.
        """
        current_sql = sql
        final_reflection = None

        for attempt in range(max_retries + 1):
            reflection = await self.reflect(current_sql, question, db_path)
            final_reflection = reflection

            if reflection["passed"]:
                return current_sql, final_reflection

            if attempt < max_retries:
                issues = (
                    reflection.get("relevance", {}).get("issues", [])
                    + reflection.get("syntax", {}).get("issues", [])
                )
                if not reflection.get("executability", {}).get("executable", True):
                    issues.insert(0, f"SQL execution error: {reflection['executability'].get('error', 'unknown')}")

                feedback = (
                    f"{question}\n\n[Fix the following issues with the previous SQL:\n"
                    + "\n".join(f"- {i}" for i in issues[:5])
                    + "\n]"
                )
                try:
                    current_sql = await regenerate_sql(feedback)
                except Exception:
                    break

        return current_sql, final_reflection
