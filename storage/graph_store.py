"""Entity graph store — MongoDB $graphLookup for event relationship traversal.

At 3 competitors and hundreds of events, MongoDB handles 2-3 hop traversal
without the operational overhead of a dedicated graph DB.

Upgrade path: if queries become slow after 10,000+ events, migrate the
entity_graph collection to Neo4j. The event store stays unchanged.

Entity graph schema (entity_graph collection):
{
  "_id": "event_id",
  "company": "Competitor A",
  "event_type": "feature_launch",
  "timestamp": "2026-06-10",
  "related_event_ids": ["event_id_2", "event_id_3"],
  "related_companies": ["Partner Corp"]
}

$graphLookup allows: "what events connect to this one?" (1 hop)
and "what events preceded this?" (2-3 hops).
"""

from __future__ import annotations

from typing import Any

import motor.motor_asyncio as motor
from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import PyMongoError

from tools.errors import ErrorCode, StorageError

COLLECTION_ENTITY_GRAPH = "entity_graph"


class GraphStore:
    """MongoDB-backed entity graph for event relationship traversal."""

    def __init__(self, mongodb_uri: str, db_name: str) -> None:
        self._uri = mongodb_uri
        self._db_name = db_name
        self._client: motor.AsyncIOMotorClient | None = None
        self._db: motor.AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        try:
            self._client = motor.AsyncIOMotorClient(
                self._uri,
                serverSelectionTimeoutMS=5000,
            )
            self._db = self._client[self._db_name]
            await self._client.admin.command("ping")
            await self._ensure_indexes()
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_CONNECTION_FAILED,
                message="Failed to connect GraphStore to MongoDB",
                cause=exc,
            ) from exc

    async def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._db = None

    def _require_db(self) -> motor.AsyncIOMotorDatabase:
        if self._db is None:
            raise StorageError(
                code=ErrorCode.STORE_CONNECTION_FAILED,
                message="GraphStore not connected. Call connect() first.",
            )
        return self._db

    async def _ensure_indexes(self) -> None:
        db = self._require_db()
        col = db[COLLECTION_ENTITY_GRAPH]
        await col.create_indexes([
            IndexModel([("company", ASCENDING)]),
            IndexModel([("event_type", ASCENDING)]),
            IndexModel([("timestamp", DESCENDING)]),
            IndexModel([("related_event_ids", ASCENDING)]),
        ])

    # ── Write ─────────────────────────────────────────────────────────────────

    async def upsert_node(
        self,
        event_id: str,
        company: str,
        event_type: str,
        timestamp: str,
        related_event_ids: list[str] | None = None,
        related_companies: list[str] | None = None,
    ) -> None:
        """Insert or update an entity graph node."""
        db = self._require_db()
        doc = {
            "_id": event_id,
            "company": company,
            "event_type": event_type,
            "timestamp": timestamp,
            "related_event_ids": related_event_ids or [],
            "related_companies": related_companies or [],
        }
        try:
            await db[COLLECTION_ENTITY_GRAPH].replace_one(
                {"_id": event_id}, doc, upsert=True
            )
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message=f"Failed to upsert graph node {event_id}",
                cause=exc,
            ) from exc

    async def add_edge(self, from_event_id: str, to_event_id: str) -> None:
        """Add a directed edge from one event to another."""
        db = self._require_db()
        try:
            await db[COLLECTION_ENTITY_GRAPH].update_one(
                {"_id": from_event_id},
                {"$addToSet": {"related_event_ids": to_event_id}},
                upsert=True,
            )
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message=f"Failed to add edge {from_event_id} → {to_event_id}",
                cause=exc,
            ) from exc

    # ── Read / traversal ──────────────────────────────────────────────────────

    async def get_related_events(
        self,
        event_id: str,
        max_depth: int = 2,
        restrict_to_company: str | None = None,
    ) -> list[dict[str, Any]]:
        """Traverse the entity graph from an event up to max_depth hops.

        Uses MongoDB $graphLookup — supports up to depth 3 efficiently at
        current scale (hundreds of events per competitor).

        Args:
            event_id: Starting event ID.
            max_depth: Maximum traversal depth (1-3 recommended).
            restrict_to_company: If set, only return nodes for this company.

        Returns:
            List of graph node documents (each has event_id, company, event_type, timestamp).
        """
        db = self._require_db()
        match_filter: dict[str, Any] = {"_id": event_id}

        pipeline: list[dict] = [
            {"$match": match_filter},
            {
                "$graphLookup": {
                    "from": COLLECTION_ENTITY_GRAPH,
                    "startWith": "$related_event_ids",
                    "connectFromField": "related_event_ids",
                    "connectToField": "_id",
                    "as": "related_nodes",
                    "maxDepth": max_depth - 1,
                    "depthField": "depth",
                }
            },
        ]

        try:
            cursor = db[COLLECTION_ENTITY_GRAPH].aggregate(pipeline)
            result = await cursor.to_list(length=1)
            if not result:
                return []

            related = result[0].get("related_nodes", [])
            if restrict_to_company:
                related = [n for n in related if n.get("company") == restrict_to_company]

            return sorted(related, key=lambda n: n.get("timestamp", ""), reverse=True)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Graph traversal failed from event {event_id}",
                cause=exc,
            ) from exc

    async def get_causal_chain(
        self,
        target_event_id: str,
        company: str,
        lookback_days: int = 180,
    ) -> list[dict[str, Any]]:
        """Return events that temporally precede the target event for the same company.

        Used by the Conversational Agent for causal chain queries.
        Not a graph traversal — a time-filtered query on company events.
        """
        db = self._require_db()
        target = await db[COLLECTION_ENTITY_GRAPH].find_one({"_id": target_event_id})
        if not target:
            return []

        from datetime import datetime, timedelta, timezone
        target_ts = target.get("timestamp", "")
        try:
            target_dt = datetime.fromisoformat(target_ts.replace("Z", "+00:00"))
            cutoff = (target_dt - timedelta(days=lookback_days)).isoformat()
        except (ValueError, TypeError):
            return []

        try:
            cursor = (
                db[COLLECTION_ENTITY_GRAPH]
                .find({
                    "company": company,
                    "timestamp": {"$gte": cutoff, "$lt": target_ts},
                    "_id": {"$ne": target_event_id},
                })
                .sort("timestamp", ASCENDING)
                .limit(50)
            )
            return await cursor.to_list(length=50)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Causal chain query failed for {company}",
                cause=exc,
            ) from exc

    async def health_check(self) -> bool:
        try:
            if self._client is None:
                return False
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False
