"""Write-only HTTP view with per-source bearer authentication."""
from __future__ import annotations

import asyncio
import json

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .reported import InvalidCredential

MAX_PAYLOAD_BYTES = 64 * 1024
MAX_BATCH_EVENTS = 100


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-JSON number")


class DailyActivityEventsView(HomeAssistantView):
    url = "/api/daily_activity/events"
    name = "api:daily_activity:events"
    # HA's general bearer middleware cannot validate our scoped credentials.
    # Every request is authenticated below before parsing or revealing sources.
    requires_auth = False

    def __init__(self, sources):
        self.sources = sources

    @staticmethod
    def response(body, status=200):
        headers = {"Cache-Control": "no-store"}
        if status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return web.json_response(body, status=status, headers=headers)

    async def post(self, request):
        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not 32 <= len(token) <= 256:
            return self.response({"error": "invalid_credentials"}, 401)
        authorized = None
        # Match only loaded sources; invalid credentials never reveal registration.
        for source in tuple(self.sources.values()):
            if source.verifies(token):
                authorized = source
        if authorized is None:
            return self.response({"error": "invalid_credentials"}, 401)
        if request.content_type != "application/json":
            return self.response({"error": "content_type_must_be_json"}, 415)
        if request.content_length is not None and request.content_length > MAX_PAYLOAD_BYTES:
            return self.response({"error": "payload_too_large"}, 413)
        try:
            body = bytearray()
            async with asyncio.timeout(10):
                async for chunk in request.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > MAX_PAYLOAD_BYTES:
                        return self.response({"error": "payload_too_large"}, 413)
            payload = json.loads(body, object_pairs_hook=_json_object,
                                 parse_constant=_reject_constant)
        except TimeoutError:
            return self.response({"error": "request_timeout"}, 408)
        except (ValueError, UnicodeError, RecursionError):
            return self.response({"error": "invalid_json"}, 400)
        if (not isinstance(payload, dict) or set(payload) != {"events"}
                or not isinstance(payload["events"], list) or not payload["events"]):
            return self.response({"error": "invalid_batch"}, 400)
        events = payload["events"]
        if len(events) > MAX_BATCH_EVENTS:
            return self.response({"error": "batch_too_large"}, 413)
        if any(not isinstance(event, dict) for event in events):
            return self.response({"error": "invalid_event"}, 400)
        # Authorization is all-or-nothing and precedes event validation/persistence.
        if any(event.get("source") != authorized.source_id for event in events):
            return self.response({"error": "unauthorized_source"}, 403)
        try:
            results = await authorized.async_ingest(events, token)
        except InvalidCredential:
            return self.response({"error": "invalid_credentials"}, 401)
        except OSError:
            # No exception text, request bodies or tokens enter responses/logs.
            return self.response({"error": "storage_error", "retry": "entire_batch"}, 503)
        status = 207 if any(item["status"] == "rejected" for item in results) else 200
        return self.response({"results": results}, status)
