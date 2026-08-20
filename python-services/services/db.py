"""MongoDB access for the Phase 2 service.

The Next.js app owns `projects`, `documents` and `jobs`. This module reads those
and owns three more collections: `issues`, `reports` and `jobstates` (the Python
side's own view of a job, which can hold fields the Mongoose schema does not
declare). Job status and progress are mirrored back into `jobs` so the existing
frontend polling keeps working unchanged.

If MongoDB is unreachable the service falls back to an in-process store so the
API stays up and single-run processing still works.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_URI = "mongodb://localhost:27017/document-correction"
DEFAULT_DB_NAME = "test"      # what Mongoose uses when the URI names no database


def _uri() -> str:
    # read lazily: .env may be loaded after this module is imported
    return os.getenv("MONGODB_URI") or DEFAULT_URI


def _timeout_ms() -> int:
    return int(os.getenv("MONGO_TIMEOUT_MS", "8000"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_indexes(database) -> None:
    """Create the indexes this service needs, retiring superseded ones.

    Issues used to be keyed by a top-level `id`; they are now keyed by
    `engine.id` so the document itself can match the frontend's schema. The old
    unique index would make every new document collide on `id: null`, so it is
    dropped if an earlier version left it behind.
    """
    try:
        existing = set(database["issues"].index_information())
    except Exception as exc:
        logger.warning("could not read index information: %s", exc)
        existing = set()

    if "jobId_1_id_1" in existing:
        try:
            database["issues"].drop_index("jobId_1_id_1")
            logger.info("dropped superseded index issues.jobId_1_id_1")
        except Exception as exc:
            logger.warning("could not drop the superseded issues index: %s", exc)

    for collection, keys, options in (
        ("issues", [("jobId", 1)], {}),
        ("issues", [("jobId", 1), ("engine.id", 1)], {"unique": True,
                                                      "name": "jobId_1_engineId_1"}),
        ("corrections", [("jobId", 1)], {}),
        ("reports", [("jobId", 1)], {"unique": True}),
        ("jobstates", [("jobId", 1)], {"unique": True}),
    ):
        try:
            database[collection].create_index(keys, **options)
        except Exception as exc:
            logger.warning("could not create index on %s %s: %s", collection, keys, exc)


_VOLATILE_KEYS = ("updatedAt", "createdAt", "engineHash", "_id")


def _content_hash(document: Dict[str, Any]) -> str:
    """Stable digest of an issue document, ignoring bookkeeping fields.

    `applied_at` is excluded as well: re-applying an unchanged correction is not
    a change to the issue, and letting it in would restamp every fixed issue on
    every rebuild. The stored value then records when a fix was *first* applied.
    """
    import copy
    import hashlib
    import json

    payload = {k: v for k, v in document.items() if k not in _VOLATILE_KEYS}
    correction = ((payload.get("engine") or {}).get("correction"))
    if isinstance(correction, dict) and "applied_at" in correction:
        payload = copy.deepcopy(payload)
        payload["engine"]["correction"].pop("applied_at", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _uses_tls(uri: str) -> bool:
    lowered = uri.lower()
    return lowered.startswith("mongodb+srv://") or "tls=true" in lowered or "ssl=true" in lowered


class MemoryStore:
    """Dictionary-backed stand-in used when MongoDB is unavailable."""

    kind = "memory"

    def __init__(self) -> None:
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.issues: Dict[str, List[Dict[str, Any]]] = {}
        self.reports: Dict[str, Dict[str, Any]] = {}
        self.corrections: Dict[str, List[Dict[str, Any]]] = {}
        self.documents: Dict[str, Dict[str, Any]] = {}

    async def connect(self) -> "MemoryStore":
        return self

    async def close(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        return self.documents.get(str(document_id))

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.jobs.get(str(job_id))

    async def upsert_job_state(self, job_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        state = self.jobs.setdefault(str(job_id), {"jobId": str(job_id)})
        if state.get("status") == "CANCELLED" and fields.get("status") == "PROCESSING":
            fields = {k: v for k, v in fields.items() if k != "status"}
        state.update(fields)
        state["updatedAt"] = _now()
        return state

    async def save_issues(self, job_id: str, issues: List[Dict[str, Any]]) -> int:
        """Store frontend-shaped documents, exactly as the Mongo backend does."""
        existing = {
            (i.get("engine") or {}).get("id"): i for i in self.issues.get(str(job_id), [])
        }
        merged = []
        for issue in issues:
            engine_id = (issue.get("engine") or {}).get("id")
            previous = existing.get(engine_id) or {}
            merged.append({**previous, **issue,
                           "_id": previous.get("_id") or engine_id})
        self.issues[str(job_id)] = merged
        return len(merged)

    async def get_issues(self, job_id: str) -> List[Dict[str, Any]]:
        from services.job_sync import STATUS_FROM_UI

        results = []
        for row in self.issues.get(str(job_id), []):
            engine = dict(row.get("engine") or {})
            engine["_id"] = row.get("_id")
            decision = STATUS_FROM_UI.get(row.get("status", ""))
            if decision is not None:
                engine["status"] = decision.value
            engine["uiStatus"] = row.get("status")
            results.append(engine)
        return results

    async def update_issue(self, job_id: str, issue_id: str,
                           fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from services.job_sync import STATUS_TO_UI

        for row in self.issues.get(str(job_id), []):
            if (row.get("engine") or {}).get("id") != issue_id:
                continue
            row.setdefault("engine", {}).update(fields)
            if "status" in fields:
                ui_status = next(
                    (ui for engine, ui in STATUS_TO_UI.items()
                     if engine.value == fields["status"]), None,
                )
                if ui_status:
                    row["status"] = ui_status
            return row
        return None

    async def save_report(self, job_id: str, report: Dict[str, Any]) -> None:
        self.reports[str(job_id)] = report

    async def get_report(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.reports.get(str(job_id))

    async def save_corrections(self, job_id: str,
                               corrections: List[Dict[str, Any]]) -> int:
        self.corrections[str(job_id)] = corrections
        return len(corrections)

    async def issue_object_ids(self, job_id: str) -> Dict[str, Any]:
        return {issue.get("id"): issue.get("id")
                for issue in self.issues.get(str(job_id), [])}

    async def job_status(self, job_id: str) -> Optional[str]:
        return (self.jobs.get(str(job_id)) or {}).get("status")

    async def claim_queued_job(self) -> Optional[Dict[str, Any]]:
        for job in self.jobs.values():
            if job.get("status") == "QUEUED":
                job["status"] = "PROCESSING"
                return job
        return None


class MongoStore:
    """pymongo access wrapped in threads so it never blocks the event loop."""

    kind = "mongodb"

    def __init__(self, uri: Optional[str] = None, db_name: Optional[str] = None):
        self.uri = uri or _uri()
        self.db_name = db_name if db_name is not None else os.getenv("MONGODB_DB", "")
        self._client = None
        self._db = None

    # ---------------------------------------------------------------- lifecycle
    async def connect(self) -> "MongoStore":
        from pymongo import MongoClient

        def _connect():
            timeout = _timeout_ms()
            options: Dict[str, Any] = {
                "serverSelectionTimeoutMS": timeout,
                "connectTimeoutMS": timeout,
                "appname": "document-correction-python",
            }
            if _uses_tls(self.uri):
                # Python on macOS has no system CA store of its own, which makes
                # every Atlas connection fail certificate verification.
                import certifi

                options["tlsCAFile"] = certifi.where()
            client = MongoClient(self.uri, **options)
            client.admin.command("ping")
            if self.db_name:
                database = client[self.db_name]
            else:
                try:
                    database = client.get_default_database()
                except Exception:
                    # A URI with no database path (common with Atlas) makes
                    # Mongoose fall back to "test", so the Python side must use
                    # the same database or the two halves would not see each other.
                    database = None
                if database is None:
                    database = client[DEFAULT_DB_NAME]
            _ensure_indexes(database)
            return client, database

        self._client, self._db = await asyncio.to_thread(_connect)
        logger.info("connected to MongoDB database '%s'", self._db.name)
        return self

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None

    async def ping(self) -> bool:
        try:
            await asyncio.to_thread(self._client.admin.command, "ping")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _object_id(value: str):
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            return ObjectId(str(value))
        except (InvalidId, TypeError):
            return None

    @staticmethod
    def _clean(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Make a Mongo document JSON-serializable."""
        if document is None:
            return None
        result = dict(document)
        if "_id" in result:
            result["_id"] = str(result["_id"])
        for key, value in list(result.items()):
            if type(value).__name__ == "ObjectId":
                result[key] = str(value)
        return result

    # -------------------------------------------------------------------- reads
    async def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        object_id = self._object_id(document_id)
        if object_id is None:
            return None
        found = await asyncio.to_thread(self._db["documents"].find_one, {"_id": object_id})
        return self._clean(found)

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Merge the Mongoose job document with the Python-side job state."""
        object_id = self._object_id(job_id)
        job = None
        if object_id is not None:
            job = await asyncio.to_thread(self._db["jobs"].find_one, {"_id": object_id})
        state = await asyncio.to_thread(
            self._db["jobstates"].find_one, {"jobId": str(job_id)}
        )
        if job is None and state is None:
            return None
        merged: Dict[str, Any] = {}
        merged.update(self._clean(job) or {})
        merged.update(self._clean(state) or {})
        merged["jobId"] = str(job_id)
        return merged

    # ------------------------------------------------------------------- writes
    async def upsert_job_state(self, job_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Persist the engine's job state and mirror it into the UI's `jobs` doc."""
        from services.job_sync import job_document, log_entry, stage_log

        payload = {k: v for k, v in fields.items() if k not in ("logMessage", "logLevel")}
        payload.update({"jobId": str(job_id), "updatedAt": _now()})
        await asyncio.to_thread(
            self._db["jobstates"].update_one,
            {"jobId": str(job_id)}, {"$set": payload}, True,
        )

        object_id = self._object_id(job_id)
        if object_id is None:
            return payload
        update: Dict[str, Any] = {}
        mirror = job_document(fields)
        if mirror:
            update["$set"] = mirror
        entry = stage_log(fields.get("stage", ""), fields)
        if fields.get("logMessage"):
            entry = log_entry(str(fields["logMessage"]), fields.get("logLevel", "INFO"))
        if entry:
            update["$push"] = {"logs": entry}
        if fields.get("status") == "FAILED" and fields.get("error"):
            update.setdefault("$push", {})["logs"] = {
                "at": _now(), "level": "ERROR", "message": str(fields["error"])[:500],
            }
        if update:
            selector: Dict[str, Any] = {"_id": object_id}
            if mirror.get("status") == "PROCESSING":
                # a cancel landing mid-stage must win over an in-flight progress write
                selector["status"] = {"$ne": "CANCELLED"}
            await asyncio.to_thread(
                self._db["jobs"].update_one, selector, update
            )
        return payload

    async def job_status(self, job_id: str) -> Optional[str]:
        """The status the frontend currently shows — used to honour cancellation."""
        object_id = self._object_id(job_id)
        if object_id is None:
            return None
        row = await asyncio.to_thread(
            self._db["jobs"].find_one, {"_id": object_id}, {"status": 1}
        )
        return row.get("status") if row else None

    async def claim_queued_job(self) -> Optional[Dict[str, Any]]:
        """Atomically take the oldest QUEUED job so two workers cannot both run it."""
        from pymongo import ReturnDocument

        row = await asyncio.to_thread(
            self._db["jobs"].find_one_and_update,
            {"status": "QUEUED"},
            {"$set": {"status": "PROCESSING", "progress": 0, "startedAt": _now()},
             "$push": {"logs": {"at": _now(), "level": "INFO",
                                "message": "Picked up by the processing service"}}},
            sort=[("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return self._clean(row)

    async def save_issues(self, job_id: str, issues: List[Dict[str, Any]]) -> int:
        """Upsert issues in the frontend's shape, keyed by the engine's own id.

        Upserting (rather than replacing) preserves each document's ``_id``,
        which is what the UI's approve/reject calls address — so a rebuild does
        not invalidate links the reviewer is looking at.
        """
        from pymongo import DeleteMany, UpdateOne

        job_oid = self._object_id(job_id) or str(job_id)
        collection = self._db["issues"]
        if not issues:
            await asyncio.to_thread(collection.delete_many, {"jobId": job_oid})
            return 0

        now = _now()
        existing = await asyncio.to_thread(
            lambda: {row.get("engine", {}).get("id"): row.get("engineHash")
                     for row in collection.find({"jobId": job_oid},
                                                {"engine.id": 1, "engineHash": 1})}
        )

        operations: List[Any] = []
        unchanged = 0
        for issue in issues:
            document = dict(issue)
            engine_id = document.get("engine", {}).get("id") or document.get("id")
            document.pop("_id", None)
            document["jobId"] = job_oid
            if isinstance(document.get("projectId"), str):
                document["projectId"] = (self._object_id(document["projectId"])
                                         or document["projectId"])
            digest = _content_hash(document)
            if existing.get(engine_id) == digest:
                unchanged += 1
                continue        # nothing about this issue changed; leave it alone
            document["engineHash"] = digest
            document["updatedAt"] = now
            operations.append(UpdateOne(
                {"jobId": job_oid, "engine.id": engine_id},
                {"$set": document, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            ))
        keep = [i.get("engine", {}).get("id") or i.get("id") for i in issues]
        operations.append(DeleteMany({"jobId": job_oid, "engine.id": {"$nin": keep}}))
        await asyncio.to_thread(collection.bulk_write, operations, False)
        if unchanged:
            logger.debug("job %s: %s issue(s) unchanged, not rewritten", job_id, unchanged)
        return len(issues)

    async def get_issues(self, job_id: str) -> List[Dict[str, Any]]:
        """Issues with the reviewer's current decision folded back in.

        The UI writes approve/reject straight into `issues.status`, so that
        column — not the engine's own copy — is the source of truth for what a
        rebuild should apply.
        """
        from services.job_sync import STATUS_FROM_UI

        job_oid = self._object_id(job_id) or str(job_id)
        rows = await asyncio.to_thread(
            lambda: list(self._db["issues"].find({"jobId": job_oid}))
        )
        results: List[Dict[str, Any]] = []
        for row in rows:
            engine = row.get("engine") or {}
            merged = dict(engine)
            merged["_id"] = str(row.get("_id"))
            decision = STATUS_FROM_UI.get(row.get("status", ""))
            if decision is not None:
                merged["status"] = decision.value
            merged.setdefault("id", engine.get("id"))
            merged["uiStatus"] = row.get("status")
            results.append(merged)
        return results

    async def update_issue(self, job_id: str, issue_id: str,
                           fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from services.job_sync import STATUS_TO_UI

        job_oid = self._object_id(job_id) or str(job_id)
        update: Dict[str, Any] = {"updatedAt": _now()}
        for key, value in fields.items():
            update[f"engine.{key}"] = value
        if "status" in fields:
            ui_status = next(
                (ui for engine, ui in STATUS_TO_UI.items() if engine.value == fields["status"]),
                None,
            )
            if ui_status:
                update["status"] = ui_status
        await asyncio.to_thread(
            self._db["issues"].update_one,
            {"jobId": job_oid, "engine.id": issue_id}, {"$set": update},
        )
        found = await asyncio.to_thread(
            self._db["issues"].find_one, {"jobId": job_oid, "engine.id": issue_id}
        )
        return self._clean(found)

    async def save_corrections(self, job_id: str,
                               corrections: List[Dict[str, Any]]) -> int:
        """Replace the applied-correction log for this job."""
        job_oid = self._object_id(job_id) or str(job_id)
        collection = self._db["corrections"]
        await asyncio.to_thread(collection.delete_many, {"jobId": job_oid})
        if not corrections:
            return 0
        now = _now()
        documents = []
        for correction in corrections:
            document = {**correction, "jobId": job_oid, "createdAt": now}
            if isinstance(document.get("projectId"), str):
                document["projectId"] = (self._object_id(document["projectId"])
                                         or document["projectId"])
            documents.append(document)
        await asyncio.to_thread(collection.insert_many, documents)
        return len(corrections)

    async def issue_object_ids(self, job_id: str) -> Dict[str, Any]:
        """engine issue id -> the `_id` the frontend addresses it by."""
        job_oid = self._object_id(job_id) or str(job_id)
        rows = await asyncio.to_thread(
            lambda: list(self._db["issues"].find({"jobId": job_oid}, {"engine.id": 1}))
        )
        return {row.get("engine", {}).get("id"): row["_id"] for row in rows}

    async def save_report(self, job_id: str, report: Dict[str, Any]) -> None:
        await asyncio.to_thread(
            self._db["reports"].replace_one,
            {"jobId": str(job_id)}, {**report, "jobId": str(job_id)}, True,
        )

    async def get_report(self, job_id: str) -> Optional[Dict[str, Any]]:
        found = await asyncio.to_thread(
            self._db["reports"].find_one, {"jobId": str(job_id)}
        )
        return self._clean(found)


_store: Optional[Any] = None


async def get_store() -> Any:
    """Process-wide store, connecting on first use and degrading to memory."""
    global _store
    if _store is not None:
        return _store
    try:
        _store = await MongoStore().connect()
    except Exception as exc:
        logger.error("MongoDB unavailable (%s); using the in-memory store", exc)
        _store = await MemoryStore().connect()
    return _store


async def close_store() -> None:
    global _store
    if _store is not None:
        await _store.close()
        _store = None
