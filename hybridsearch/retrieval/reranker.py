"""Cross-encoder reranker for hybrid search results.

Reuses the same quora-roberta-large model as t2sql/base.py for
consistent reranking across both retrieval approaches.
"""


class Reranker:
    """Re-ranks search results using the cross-encoder from sentence-transformers.

    Wraps the same ``cross-encoder/quora-roberta-large`` model used by the
    existing ChromaDB agent so both systems produce comparable scores.
    """

    _model = None

    @classmethod
    def get_model(cls):
        """Lazy singleton cross-encoder."""
        if cls._model is None:
            from sentence_transformers import CrossEncoder

            cls._model = CrossEncoder("cross-encoder/quora-roberta-large")
        return cls._model

    @classmethod
    def rerank(
        cls,
        query: str,
        candidates: list[dict],
        candidate_text_key: str = "question",
        score_key: str = "rerank_score",
        min_score: float = 0.0,
    ) -> list[dict]:
        """Re-rank candidates by cross-encoder similarity to the query.

        Args:
            query: The user's question (reference text).
            candidates: List of candidate dicts, each containing *candidate_text_key*.
            candidate_text_key: Key in candidates holding the text to compare.
            score_key: Key where the rerank score will be stored.
            min_score: Minimum cross-encoder score to keep a candidate.

        Returns:
            Candidates with ``score_key`` added, sorted descending by score.
        """
        if not candidates:
            return candidates

        try:
            texts = [c.get(candidate_text_key, "") for c in candidates]
            rank_result = cls.get_model().rank(query, texts)
        except Exception:
            # Cross-encoder unavailable — return candidates unchanged
            return candidates

        # Map corpus_id → score
        score_map = {r["corpus_id"]: r["score"] for r in rank_result}

        for i, candidate in enumerate(candidates):
            candidate[score_key] = score_map.get(i, 0.0)

        # Filter and sort
        reranked = [c for c in candidates if c.get(score_key, 0) >= min_score]
        reranked.sort(key=lambda c: c.get(score_key, 0), reverse=True)

        return reranked
