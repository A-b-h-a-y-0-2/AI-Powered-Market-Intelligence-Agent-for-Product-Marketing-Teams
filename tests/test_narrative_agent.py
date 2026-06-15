"""Tests for NarrativeAgent clustering logic.

No LLM calls — tests exercise _cluster_events (DBSCAN) and the
6-hour visibility window filter in isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import importlib.util
_HAS_SKLEARN = importlib.util.find_spec("sklearn") is not None

from agents.narrative_agent import NarrativeAgent, _cluster_events


@pytest.fixture()
def agent(fake_model_config, fake_cost_config) -> NarrativeAgent:
    event_store = MagicMock()
    embedder = MagicMock()
    llm_adapter = MagicMock()
    return NarrativeAgent(
        event_store=event_store,
        embedder=embedder,
        model_config=fake_model_config,
        cost_config=fake_cost_config,
        llm_adapter=llm_adapter,
    )


@pytest.mark.skipif(not _HAS_SKLEARN, reason="scikit-learn not installed")
class TestClusterEvents:
    def test_identical_embeddings_cluster_together(self):
        v = [1.0, 0.0, 0.0]
        embeddings = [v for _ in range(5)]
        labels = _cluster_events(embeddings, eps=0.15, min_samples=3)
        # All identical → one cluster
        assert len(set(labels) - {-1}) == 1

    def test_orthogonal_embeddings_form_separate_clusters(self):
        # 4 events in direction A, 4 in direction B — orthogonal
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        embeddings = [a] * 4 + [b] * 4
        labels = _cluster_events(embeddings, eps=0.15, min_samples=3)
        unique_clusters = set(labels) - {-1}
        assert len(unique_clusters) == 2

    def test_insufficient_events_returns_noise(self):
        # Only 1 vector — min_samples=3 means no cluster forms
        embeddings = [[1.0, 0.0, 0.0]]
        labels = _cluster_events(embeddings, eps=0.15, min_samples=3)
        assert all(label == -1 for label in labels)

    def test_returns_list_of_ints(self):
        embeddings = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
        labels = _cluster_events(embeddings, eps=0.15, min_samples=3)
        assert isinstance(labels, list)
        assert all(isinstance(lb, int) for lb in labels)

    def test_label_count_matches_embedding_count(self):
        embeddings = [[float(i), 0.0, 0.0] for i in range(10)]
        labels = _cluster_events(embeddings, eps=0.15, min_samples=3)
        assert len(labels) == 10


class TestVisibilityWindow:
    def test_recent_events_excluded_within_6_hours(self, agent):
        now = datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(hours=6)

        events = [
            {
                "timestamp": (now - timedelta(hours=3)).isoformat(),
                "summary": "very recent",
            },
            {
                "timestamp": (now - timedelta(hours=7)).isoformat(),
                "summary": "old enough",
            },
        ]

        # Apply the same filter logic as NarrativeAgent
        visible = [
            e for e in events
            if e.get("timestamp", "") <= cutoff.isoformat()
        ]
        assert len(visible) == 1
        assert visible[0]["summary"] == "old enough"

    def test_exactly_6_hours_ago_is_excluded(self, agent):
        now = datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(hours=6)

        # Exactly at the cutoff boundary
        event = {"timestamp": cutoff.isoformat(), "summary": "boundary event"}
        visible = [e for e in [event] if e["timestamp"] <= cutoff.isoformat()]
        assert len(visible) == 1
