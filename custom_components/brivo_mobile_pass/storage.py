"""Verified, private credential writes through Home Assistant's Store."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

from homeassistant.helpers.storage import Store

from .api import BrivoError


class _PrivateStorageLog(logging.Filter):
    """Redact registered credential stores for the process's full write lifetime."""

    def __init__(self) -> None:
        super().__init__()
        self._identifiers: set[str] = set()

    def register(self, store: Store) -> None:
        """Keep exact identities, not Store/HA objects or credential data.

        Executor jobs and shutdown-final writes can outlive the saving coroutine
        and its HA instance. Do not expire identities at coroutine/unload/stop:
        there is no guarantee all writers have finished then. One shared filter
        per logger and a set cost O(distinct keys/paths) over the process lifetime,
        not O(saves); repeated saves and replacement Stores add no duplicates.
        """
        self._identifiers.update((store.key, store.path))
        for name in ("homeassistant.helpers.storage", "homeassistant.util.file"):
            logging.getLogger(name).addFilter(self)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and any(
            isinstance(arg, str) and arg in self._identifiers for arg in record.args
        ):
            record.msg = "Brivo credential storage operation"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_PRIVATE_STORAGE_LOG = _PrivateStorageLog()


async def async_save_credentials(store: Store, data: dict) -> None:
    """Return only when the actual file contains the expected Store envelope.

    Store.async_save can log and swallow write/serialization failures, or defer
    writes during shutdown. Never treat its return or pending/cache data as
    proof of persistence. Callers retain pending credentials on any failure.
    """
    expected = {
        "version": store.version,
        "minor_version": store.minor_version,
        "key": store.key,
        "data": deepcopy(data),
    }
    path = Path(store.path)

    def matches_disk() -> bool:
        with path.open(encoding="utf-8") as file:
            return json.load(file) == expected

    # Register before Store can schedule work. Exact identity matching leaves
    # unrelated stores/records intact, even after this coroutine has exited.
    _PRIVATE_STORAGE_LOG.register(store)
    try:
        await store.async_save(expected["data"])
        if not await store.hass.async_add_executor_job(matches_disk):
            raise BrivoError("Could not save Brivo credentials", category="credentials_persistence")
    except Exception:
        raise BrivoError(
            "Could not save Brivo credentials", category="credentials_persistence"
        ) from None
