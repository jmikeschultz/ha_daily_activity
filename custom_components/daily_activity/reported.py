"""Transactional, bounded historical interval ledger for a single source."""
from __future__ import annotations

import asyncio
from bisect import bisect_left
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import re
import secrets
from zoneinfo import ZoneInfo


from .const import (
    CONF_RETENTION_DAYS, CONF_SOURCE_ID, CONF_TIME_ZONE, CONF_TOKEN_HASH,
    CONF_TRANSITION_DIRECTION, DEFAULT_RETENTION_DAYS,
    DEFAULT_TRANSITION_DIRECTION, DOMAIN, TRANSITION_OFF_TO_ON,
)
from .durations import add_duration
from .storage import DurableStore, StorageFailure

_LOGGER = logging.getLogger(__name__)

MAX_INTERVAL_SECONDS = 31 * 86400
MAX_RECORDS = 10000
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


class EventError(ValueError):
    def __init__(self, code: str, status: int = 422):
        self.code = code
        self.status = status
        super().__init__(code)


class InvalidCredential(Exception):
    """Missing, revoked, or rotated ingestion credential."""


def timestamp(value) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise EventError("invalid_timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise EventError("invalid_timestamp") from None


def parse_event(raw: dict, source: str, now: datetime) -> dict:
    if set(raw) != {"source", "event_id", "state", "start", "end"}:
        raise EventError("invalid_event_fields")
    if raw["source"] != source:
        raise EventError("unauthorized_source", 403)
    if not isinstance(raw["event_id"], str) or not ID_PATTERN.fullmatch(raw["event_id"]):
        raise EventError("invalid_event_id")
    if raw["state"] not in ("on", "off", "unknown"):
        raise EventError("invalid_state")
    start, end = timestamp(raw["start"]), timestamp(raw["end"])
    if start >= end:
        raise EventError("invalid_interval_order")
    if (end - start).total_seconds() > MAX_INTERVAL_SECONDS:
        raise EventError("interval_too_long")
    if end > now:
        raise EventError("future_interval")
    return {"event_id": raw["event_id"], "state": raw["state"],
            "start": start.isoformat(), "end": end.isoformat()}


def rebuild(records: dict, tz: ZoneInfo, direction: str, cutoff: datetime) -> dict:
    history, durations = {}, {}
    previous = None
    latest = None
    outage = None
    expected = ("off", "on") if direction == TRANSITION_OFF_TO_ON else ("on", "off")
    for item in sorted(records.values(), key=lambda row: row["start"]):
        start, end = timestamp(item["start"]), timestamp(item["end"])
        add_duration(durations, item["state"], max(start, cutoff), end, tz)
        if (previous is not None and previous["end"] == item["start"]
                and (previous["state"], item["state"]) == expected and start >= cutoff):
            day = start.astimezone(tz).date().isoformat()
            history[day] = history.get(day, 0) + 1
            latest = start.astimezone(tz).isoformat()
        adjacent = previous is not None and previous["end"] == item["start"]
        if adjacent and previous["state"] == "on" and item["state"] == "off":
            outage = {"start": start.isoformat(), "end": None,
                      "seconds": None, "status": "awaiting_report"}
        elif outage and outage["status"] == "awaiting_report":
            if adjacent and previous["state"] == "off" and item["state"] == "on":
                outage = {**outage, "end": start.isoformat(), "status": "completed",
                          "seconds": (start - timestamp(outage["start"])).total_seconds()}
            elif not adjacent or item["state"] != "off":
                outage = {**outage, "status": "interrupted"}
        previous = item
    return {"history": history, "durations": durations, "last_activity": latest,
            "last_outage": outage}


class ReportedSource:
    """One lock and atomic Store snapshot cover credentials, ledger and totals."""

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}
        self.source_id = entry.data[CONF_SOURCE_ID]
        self.tz = ZoneInfo(cfg[CONF_TIME_ZONE])
        self.direction = cfg.get(CONF_TRANSITION_DIRECTION, DEFAULT_TRANSITION_DIRECTION)
        self.retention = int(cfg.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
        self.store = DurableStore(hass, f"{DOMAIN}.{entry.entry_id}.reported")
        self.lock = asyncio.Lock()
        self.listeners = set()
        self.active = False
        self.snapshot = {"token_hash": cfg.get(CONF_TOKEN_HASH), "events": {},
                         "history": {}, "durations": {}, "last_activity": None}

    def cutoff(self, now: datetime) -> datetime:
        day = now.astimezone(self.tz).date() - timedelta(days=self.retention)
        return datetime.combine(day, datetime.min.time(), self.tz).astimezone(timezone.utc)

    def verifies(self, token: str) -> bool:
        stored = self.snapshot.get("token_hash")
        # Compare even revoked hashes; a high-entropy random token needs no slow KDF.
        matches = hmac.compare_digest(token_hash(token), stored or "0" * 64)
        return self.active and stored is not None and matches

    async def _prepare(self, snapshot: dict, now: datetime) -> dict:
        cutoff = self.cutoff(now)
        def work():
            candidate = deepcopy(snapshot)
            # Keep an interval ending exactly at cutoff as a boundary witness.
            candidate["events"] = {key: event for key, event in candidate["events"].items()
                                   if timestamp(event["end"]) >= cutoff}
            aggregate = rebuild(candidate["events"], self.tz, self.direction, cutoff)
            if aggregate["last_activity"] is None:
                aggregate["last_activity"] = candidate.get("last_activity")
            if aggregate["last_outage"] is None:
                aggregate["last_outage"] = candidate.get("last_outage")
            candidate.update(aggregate)
            return candidate
        return await self.hass.async_add_executor_job(work)

    async def _commit(self, candidate: dict) -> None:
        async def save():
            await self.store.async_save(candidate)
            self.snapshot = candidate
        task = self.hass.async_create_task(save())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep our lock until any executor write finishes. A dropped HTTP
            # connection may commit but never release a competing write early.
            await task
            raise

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored is None and self.entry.data.get("reported_initialized"):
            raise StorageFailure("ledger_missing")
        candidate = await self._prepare(stored if stored is not None else self.snapshot,
                                        datetime.now(timezone.utc))
        # Fail setup closed on storage errors; do not silently reset a ledger.
        await self._commit(candidate)
        self.active = True

    async def async_close(self) -> None:
        async with self.lock:
            self.active = False
            self.listeners.clear()

    async def async_credential(self, revoke: bool = False) -> str | None:
        async with self.lock:
            if not self.active:
                raise InvalidCredential
            token, hashed = (None, None) if revoke else new_token()
            candidate = deepcopy(self.snapshot)
            candidate["token_hash"] = hashed
            await self._commit(candidate)
            return token

    async def async_prune(self) -> None:
        async with self.lock:
            if not self.active:
                return
            candidate = await self._prepare(self.snapshot, datetime.now(timezone.utc))
            if candidate != self.snapshot:
                await self._commit(candidate)
            self._notify()

    def _notify(self):
        for listener in tuple(self.listeners):
            try:
                listener()
            except Exception:  # Sensor publication must not invalidate a committed ack.
                _LOGGER.error("Could not publish reported activity sensor update")

    async def async_ingest(self, events: list[dict], token: str) -> list[dict]:
        async with self.lock:
            # Rotation/revocation/unload wins over previously authenticated requests.
            if not self.verifies(token):
                raise InvalidCredential
            now = datetime.now(timezone.utc)
            candidate = await self._prepare(self.snapshot, now)
            cutoff = self.cutoff(now)
            def process():
                records = candidate["events"]
                ordered = sorted(records.values(), key=lambda event: event["start"])
                starts = [event["start"] for event in ordered]
                results = []
                for index, raw in enumerate(events):
                    result = {"index": index, "event_id": (raw.get("event_id") if isinstance(raw.get("event_id"), str)
                              and ID_PATTERN.fullmatch(raw["event_id"]) else None)}
                    try:
                        event = parse_event(raw, self.source_id, now)
                        existing = records.get(event["event_id"])
                        if existing is not None:
                            if existing != event:
                                raise EventError("event_id_conflict", 409)
                            result.update(status="duplicate", http_status=200)
                        else:
                            if timestamp(event["end"]) <= cutoff:
                                raise EventError("interval_expired")
                            pos = bisect_left(starts, event["start"])
                            if ((pos > 0 and ordered[pos-1]["end"] > event["start"])
                                    or (pos < len(ordered) and ordered[pos]["start"] < event["end"])):
                                raise EventError("interval_overlap", 409)
                            if len(records) >= MAX_RECORDS:
                                raise EventError("source_capacity", 429)
                            records[event["event_id"]] = event
                            ordered.insert(pos, event)
                            starts.insert(pos, event["start"])
                            result.update(status="accepted", http_status=201)
                    except EventError as error:
                        result.update(status="rejected", http_status=error.status, error=error.code)
                    results.append(result)
                previous_outage = candidate.get("last_outage")
                candidate.update(rebuild(records, self.tz, self.direction, cutoff))
                if candidate["last_outage"] is None:
                    candidate["last_outage"] = previous_outage
                if candidate["last_activity"] is None:
                    candidate["last_activity"] = self.snapshot.get("last_activity")
                return results
            results = await self.hass.async_add_executor_job(process)
            if candidate != self.snapshot:
                # Single atomic write; no in-memory publication before successful save.
                await self._commit(candidate)
                self._notify()
            return results
