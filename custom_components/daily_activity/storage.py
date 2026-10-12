"""Verified durable Store writes for acknowledged producer submissions."""
import json
import os
from pathlib import Path

from homeassistant.helpers.storage import Store


class StorageFailure(OSError):
    """The ledger could not be loaded or durably written."""


class DurableStore(Store):
    """Store with atomic writes and disk verification, including fsync.

    HA Store may log write failures instead of raising them, or defer writes at
    shutdown. Never acknowledge a producer merely because async_save returned.
    """

    def __init__(self, hass, key):
        super().__init__(hass, 1, key, private=True, atomic_writes=True,
                         serialize_in_event_loop=False)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise StorageFailure("ledger_read_failed") from None

    async def async_load(self):
        # Fail closed rather than allowing Store's corrupt-file recovery to erase
        # deduplication state or resurrect a revoked bootstrap credential.
        await self.hass.async_add_executor_job(self._read)
        return await super().async_load()

    def _verify(self, expected):
        envelope = self._read()
        if envelope is None or envelope.get("data") != expected:
            raise StorageFailure("ledger_write_failed")
        try:
            with open(self.path, "rb") as file:
                os.fsync(file.fileno())
            descriptor = os.open(Path(self.path).parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise StorageFailure("ledger_sync_failed") from None

    async def async_save(self, data):
        await super().async_save(data)
        await self.hass.async_add_executor_job(self._verify, data)
