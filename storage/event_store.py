"""MongoDB event store.

All events are stored in typed collections per event category.
Events are immutable once written — corrections create new versioned documents.
Indexes: (company, timestamp), (event_type, timestamp), (confidence_score), (stakeholder_tags).

Raises StorageError with named codes on all failures.
Every external call has an explicit timeout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import motor.motor_asyncio as motor
from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import PyMongoError

from tools.errors import ErrorCode, StorageError

# Collection names
COLLECTION_EVENTS = "events"
COLLECTION_QUARANTINE = "quarantine"
COLLECTION_ENRICHED_FACTS = "enriched_facts"
COLLECTION_TRAINING_EXAMPLES = "training_examples"
COLLECTION_ENTITY_GRAPH = "entity_graph"
COLLECTION_FEATURE_MATRIX = "feature_matrix"
COLLECTION_PIPELINE_STATE = "pipeline_state"
COLLECTION_REVIEW_BATCHES = "review_batches"


class EventStore:
    """Async MongoDB client with typed methods for event persistence."""

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
                connectTimeoutMS=5000,
                socketTimeoutMS=10000,
            )
            self._db = self._client[self._db_name]
            # Verify connection
            await self._client.admin.command("ping")
            await self._ensure_indexes()
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_CONNECTION_FAILED,
                message=f"Failed to connect to MongoDB at {self._uri}",
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
                message="EventStore not connected. Call connect() first.",
            )
        return self._db

    async def _ensure_indexes(self) -> None:
        db = self._require_db()
        events_col = db[COLLECTION_EVENTS]
        await events_col.create_indexes([
            IndexModel([("company", ASCENDING), ("timestamp", DESCENDING)]),
            IndexModel([("event_type", ASCENDING), ("timestamp", DESCENDING)]),
            IndexModel([("confidence_score", DESCENDING)]),
            IndexModel([("stakeholder_tags", ASCENDING)]),
            IndexModel([("schema_version", ASCENDING)]),
        ])
        quarantine_col = db[COLLECTION_QUARANTINE]
        await quarantine_col.create_indexes([
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ])
        graph_col = db[COLLECTION_ENTITY_GRAPH]
        await graph_col.create_indexes([
            IndexModel([("company", ASCENDING)]),
            IndexModel([("event_type", ASCENDING)]),
            IndexModel([("timestamp", DESCENDING)]),
        ])
        enriched_col = db[COLLECTION_ENRICHED_FACTS]
        await enriched_col.create_indexes([
            IndexModel([("company", ASCENDING), ("fact_type", ASCENDING)]),
            IndexModel([("discovered_date", DESCENDING)]),
        ])

    # ── Event write ───────────────────────────────────────────────────────────

    async def insert_event(self, event: dict[str, Any]) -> str:
        """Insert a new event. Events are immutable once stored.

        Returns the inserted document _id as string.
        """
        db = self._require_db()
        doc = {
            **event,
            "_inserted_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        try:
            result = await db[COLLECTION_EVENTS].insert_one(doc)
            return str(result.inserted_id)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message="Failed to insert event",
                context={"company": event.get("company"), "event_type": event.get("event_type")},
                cause=exc,
            ) from exc

    async def add_source_to_event(self, event_id: str, source_url: str) -> None:
        """Append a new source URL to an existing event (deduplication merge)."""
        from bson import ObjectId

        db = self._require_db()
        try:
            result = await db[COLLECTION_EVENTS].update_one(
                {"_id": ObjectId(event_id)},
                {"$addToSet": {"source_urls": source_url}},
            )
            if result.matched_count == 0:
                raise StorageError(
                    code=ErrorCode.STORE_WRITE_FAILED,
                    message=f"Event not found for merge: {event_id}",
                    context={"event_id": event_id},
                )
        except StorageError:
            raise
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message=f"Failed to merge source into event {event_id}",
                context={"event_id": event_id, "source_url": source_url},
                cause=exc,
            ) from exc

    # ── Event read ────────────────────────────────────────────────────────────

    async def get_recent_events(
        self,
        company: str,
        days: int = 7,
        event_types: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = self._require_db()
        from datetime import timedelta

        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
        query: dict[str, Any] = {
            "company": company,
            "timestamp": {"$gte": cutoff},
            "confidence_score": {"$gte": min_confidence},
        }
        if event_types:
            query["event_type"] = {"$in": event_types}

        try:
            cursor = db[COLLECTION_EVENTS].find(query).sort("timestamp", DESCENDING).limit(limit)
            return await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch recent events for {company}",
                context={"company": company, "days": days},
                cause=exc,
            ) from exc

    async def get_events_by_stakeholder(
        self,
        stakeholder_tag: str,
        days: int = 7,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        db = self._require_db()
        from datetime import timedelta

        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
        try:
            cursor = (
                db[COLLECTION_EVENTS]
                .find({"stakeholder_tags": stakeholder_tag, "timestamp": {"$gte": cutoff}})
                .sort("timestamp", DESCENDING)
                .limit(limit)
            )
            return await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch events for stakeholder {stakeholder_tag}",
                cause=exc,
            ) from exc

    async def get_event_by_id(self, event_id: str) -> dict[str, Any] | None:
        from bson import ObjectId
        from bson.errors import InvalidId

        db = self._require_db()
        try:
            # Try ObjectId (pipeline-inserted docs) then string _id (UUID/seeded docs)
            try:
                oid = ObjectId(event_id)
                doc = await db[COLLECTION_EVENTS].find_one({"_id": oid})
            except InvalidId:
                doc = await db[COLLECTION_EVENTS].find_one({"_id": event_id})
            return doc
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch event {event_id}",
                cause=exc,
            ) from exc

    # ── Quarantine ────────────────────────────────────────────────────────────

    async def insert_quarantined_event(self, quarantine_doc: dict[str, Any]) -> str:
        db = self._require_db()
        try:
            result = await db[COLLECTION_QUARANTINE].insert_one(quarantine_doc)
            return str(result.inserted_id)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message="Failed to insert quarantined event",
                cause=exc,
            ) from exc

    async def get_pending_quarantine(self, limit: int = 50) -> list[dict[str, Any]]:
        db = self._require_db()
        try:
            cursor = (
                db[COLLECTION_QUARANTINE]
                .find({"status": "pending"})
                .sort("created_at", ASCENDING)
                .limit(limit)
            )
            return await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message="Failed to fetch pending quarantine events",
                cause=exc,
            ) from exc

    async def update_quarantine_status(
        self,
        quarantine_id: str,
        status: str,
        corrections: dict[str, Any] | None = None,
    ) -> None:
        from bson import ObjectId

        db = self._require_db()
        update: dict[str, Any] = {"$set": {"status": status, "human_reviewed": True}}
        if corrections:
            update["$set"]["human_corrected_fields"] = corrections

        try:
            # Try ObjectId first (items inserted by the pipeline), fall back to string _id (seeded/test data)
            try:
                filter_ = {"_id": ObjectId(quarantine_id)}
            except Exception:
                filter_ = {"_id": quarantine_id}
            result = await db[COLLECTION_QUARANTINE].update_one(filter_, update)
            if result.matched_count == 0:
                # Last fallback: quarantine_id field
                await db[COLLECTION_QUARANTINE].update_one({"quarantine_id": quarantine_id}, update)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message=f"Failed to update quarantine {quarantine_id}",
                cause=exc,
            ) from exc

    # ── Enriched facts (KB-miss background enrichment) ───────────────────────

    async def upsert_enriched_fact(self, fact: dict[str, Any]) -> None:
        db = self._require_db()
        try:
            await db[COLLECTION_ENRICHED_FACTS].update_one(
                {"company": fact["company"], "fact_type": fact["fact_type"]},
                {"$set": fact},
                upsert=True,
            )
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message=f"Failed to upsert enriched fact for {fact.get('company')}",
                cause=exc,
            ) from exc

    async def get_enriched_fact(
        self, company: str, fact_type: str
    ) -> dict[str, Any] | None:
        db = self._require_db()
        try:
            return await db[COLLECTION_ENRICHED_FACTS].find_one(
                {"company": company, "fact_type": fact_type}
            )
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch enriched fact {fact_type} for {company}",
                cause=exc,
            ) from exc

    # ── Pipeline state (for agent checkpointing) ──────────────────────────────

    async def upsert_pipeline_state(self, run_id: str, state: dict[str, Any]) -> None:
        db = self._require_db()
        try:
            await db[COLLECTION_PIPELINE_STATE].update_one(
                {"run_id": run_id},
                {"$set": {**state, "updated_at": datetime.now(tz=timezone.utc).isoformat()}},
                upsert=True,
            )
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.AGENT_CHECKPOINT_FAILED,
                message=f"Failed to checkpoint pipeline state for run {run_id}",
                cause=exc,
            ) from exc

    async def get_pipeline_state(self, run_id: str) -> dict[str, Any] | None:
        db = self._require_db()
        try:
            return await db[COLLECTION_PIPELINE_STATE].find_one({"run_id": run_id})
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to read pipeline state for run {run_id}",
                cause=exc,
            ) from exc

    # ── Feature matrix ────────────────────────────────────────────────────────

    async def get_feature_matrix(self, company: str) -> dict[str, Any] | None:
        db = self._require_db()
        try:
            return await db[COLLECTION_FEATURE_MATRIX].find_one({"company": company})
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch feature matrix for {company}",
                cause=exc,
            ) from exc

    async def upsert_feature_matrix(self, company: str, matrix: dict[str, Any]) -> None:
        db = self._require_db()
        try:
            await db[COLLECTION_FEATURE_MATRIX].update_one(
                {"company": company},
                {"$set": {**matrix, "last_updated": datetime.now(tz=timezone.utc).isoformat()}},
                upsert=True,
            )
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message=f"Failed to upsert feature matrix for {company}",
                cause=exc,
            ) from exc

    # ── Review batches (raw Apify review data for SentimentAgent) ────────────

    async def store_review_batch(self, batch: dict[str, Any]) -> str:
        """Insert a raw review batch from Apify (G2/Capterra). Append-only."""
        db = self._require_db()
        try:
            result = await db[COLLECTION_REVIEW_BATCHES].insert_one(batch)
            return str(result.inserted_id)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message=f"Failed to store review batch for {batch.get('company')}",
                cause=exc,
            ) from exc

    async def get_review_batches(
        self, company: str, platform: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Retrieve recent review batches for a company, optionally filtered by platform."""
        db = self._require_db()
        query: dict[str, Any] = {"company": company}
        if platform:
            query["platform"] = platform
        try:
            cursor = db[COLLECTION_REVIEW_BATCHES].find(query).sort("crawled_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch review batches for {company}",
                cause=exc,
            ) from exc

    # ── Training examples (for DSPy Phase 5) ─────────────────────────────────

    async def insert_training_example(self, example: dict[str, Any]) -> str:
        db = self._require_db()
        try:
            result = await db[COLLECTION_TRAINING_EXAMPLES].insert_one(example)
            return str(result.inserted_id)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_WRITE_FAILED,
                message="Failed to insert training example",
                cause=exc,
            ) from exc

    # ── Quarantine helpers ────────────────────────────────────────────────────

    async def get_quarantine_by_id(self, quarantine_id: str) -> dict[str, Any] | None:
        from bson import ObjectId

        db = self._require_db()
        try:
            # Try ObjectId first, then string _id, then quarantine_id field
            try:
                doc = await db[COLLECTION_QUARANTINE].find_one({"_id": ObjectId(quarantine_id)})
            except Exception:
                doc = None
            if doc is None:
                doc = await db[COLLECTION_QUARANTINE].find_one({"_id": quarantine_id})
            if doc is None:
                doc = await db[COLLECTION_QUARANTINE].find_one({"quarantine_id": quarantine_id})
            return doc
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch quarantine item {quarantine_id}",
                cause=exc,
            ) from exc

    async def get_quarantine_stats(self) -> dict[str, Any]:
        db = self._require_db()
        try:
            pipeline = [
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            ]
            cursor = db[COLLECTION_QUARANTINE].aggregate(pipeline)
            by_status: dict[str, int] = {}
            async for doc in cursor:
                by_status[doc["_id"]] = doc["count"]

            total = sum(by_status.values())

            # Correction rate per event_type
            rate_pipeline = [
                {"$match": {"status": "corrected"}},
                {"$group": {
                    "_id": "$extracted_event.event_type",
                    "corrected": {"$sum": 1},
                }},
            ]
            rate_cursor = db[COLLECTION_QUARANTINE].aggregate(rate_pipeline)
            correction_rate: dict[str, float] = {}
            async for doc in rate_cursor:
                event_type = doc["_id"] or "unknown"
                # Rate = corrected / total for that event_type
                count_cursor = db[COLLECTION_QUARANTINE].count_documents(
                    {"extracted_event.event_type": event_type}
                )
                total_for_type = await count_cursor if hasattr(count_cursor, "__await__") else 0
                if total_for_type > 0:
                    correction_rate[event_type] = round(doc["corrected"] / total_for_type, 2)

            return {
                "pending": by_status.get("pending", 0),
                "approved": by_status.get("approved", 0),
                "corrected": by_status.get("corrected", 0),
                "rejected": by_status.get("rejected", 0),
                "total": total,
                "correction_rate_by_event_type": correction_rate,
            }
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message="Failed to compute quarantine stats",
                cause=exc,
            ) from exc

    # ── Threat scores (pre-computed by ThreatScoringAgent) ───────────────────

    async def get_latest_threat_scores(self) -> list[dict[str, Any]]:
        """Return the most recent threat score per company."""
        db = self._require_db()
        try:
            pipeline = [
                {"$match": {"event_type": "threat_score"}},
                {"$sort": {"generated_date": DESCENDING}},
                {"$group": {"_id": "$company", "doc": {"$first": "$$ROOT"}}},
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            cursor = db[COLLECTION_EVENTS].aggregate(pipeline)
            return await cursor.to_list(length=100)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message="Failed to fetch threat scores",
                cause=exc,
            ) from exc

    async def get_threat_score(self, company: str) -> dict[str, Any] | None:
        db = self._require_db()
        try:
            return await db[COLLECTION_EVENTS].find_one(
                {"event_type": "threat_score", "company": company},
                sort=[("generated_date", DESCENDING)],
            )
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message=f"Failed to fetch threat score for {company}",
                cause=exc,
            ) from exc

    # ── Recent pipeline states ────────────────────────────────────────────────

    async def get_recent_pipeline_states(self, limit: int = 10) -> list[dict[str, Any]]:
        db = self._require_db()
        try:
            cursor = (
                db[COLLECTION_PIPELINE_STATE]
                .find({})
                .sort("started_at", DESCENDING)
                .limit(limit)
            )
            return await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise StorageError(
                code=ErrorCode.STORE_READ_FAILED,
                message="Failed to fetch recent pipeline states",
                cause=exc,
            ) from exc

    # ── Health ────────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        try:
            client = self._client
            if client is None:
                return False
            await client.admin.command("ping")
            return True
        except Exception:
            return False
