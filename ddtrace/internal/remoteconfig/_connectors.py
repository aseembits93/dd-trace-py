from dataclasses import asdict
import json
import os
import time
from typing import List
from typing import Sequence
from uuid import UUID

from ddtrace.internal.compat import get_mp_context
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload


log = get_logger(__name__)

# Size of the shared variable.
# It must be large enough to receive at least 2500 IPs or 2500 users to block.
SHARED_MEMORY_SIZE = 0x100000

SharedDataType = List[Payload]


class UUIDEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, UUID):
            # if the obj is uuid, we simply return the value of uuid
            return o.hex
        return json.JSONEncoder.default(self, o)


class _DummySharedArray:
    """Dummy shared array to be used when shared memory is not available.
    This class is used to avoid breaking the code when shared memory is not available.
    """

    def __init__(self):
        self.value = b""


class PublisherSubscriberConnector:
    """ "PublisherSubscriberConnector is the bridge between Publisher and Subscriber class that uses an array of chars
    to share information between processes. `multiprocessing.Array``, as far as we know, was the most efficient way to
    share information. We compare this approach with: Multiprocess Manager, Multiprocess Value, Multiprocess Queues
    """

    def __init__(self):
        # Avoid repeated attribute lookups for faster assignment
        data = None
        try:
            # Access SHARED_MEMORY_SIZE directly from the already-imported module namespace
            # This is slightly faster than indirect lookup if SHARED_MEMORY_SIZE is imported at module-level
            from ddtrace.internal.remoteconfig._connectors import (
                SHARED_MEMORY_SIZE, log)
            data = get_mp_context().Array("c", SHARED_MEMORY_SIZE, lock=False)
        except FileNotFoundError:
            # Only import log if exception occurs to save import time in normal cases
            if 'log' not in locals():
                from ddtrace.internal.remoteconfig._connectors import log
            log.warning(
                "Unable to create shared memory. Features relying on remote configuration will not work as expected."
            )
            # Lazy import: _DummySharedArray only on fallback
            from ddtrace.internal.remoteconfig._connectors import \
                _DummySharedArray
            data = _DummySharedArray()
        self.data = data
        self.checksum = -1  # Checksum attr validates if the Publisher send new data
        self.shared_data_counter = 0  # shared_data_counter attr validates if the Subscriber send new data
        self.read_pid = os.getpid()

    @staticmethod
    def _hash_config(payload_sequence: Sequence[Payload]):
        # Micro-optimization: local variable hydration to avoid repeated global lookups
        result = 0
        _hash = hash
        for payload in payload_sequence:
            result ^= _hash(payload.metadata)
            # Inline reference to payload.content for multiplied fast attribute lookup
            if payload.content is None:
                result <<= 1
        return result

    def read(self) -> SharedDataType:
        config_raw = self.data.value.decode("utf-8", errors="ignore")
        config = json.loads(config_raw) if config_raw else None
        if config is not None:
            shared_data_counter = config["shared_data_counter"]
            if (current_pid := os.getpid()) != self.read_pid:
                self.read_pid = current_pid
                self.shared_data_counter = 0
            if shared_data_counter != self.shared_data_counter:
                self.shared_data_counter = shared_data_counter
                return [Payload(**value) for value in config["payload_list"]]
        return []

    def write(self, payload_list: Sequence[Payload]) -> None:
        last_checksum = self._hash_config(payload_list)
        if last_checksum != self.checksum:
            data = self.serialize(payload_list)
            data_len = len(data)
            if data_len >= (SHARED_MEMORY_SIZE - 1000):
                log.warning("Datadog Remote Config shared data is %s/%s", data_len, SHARED_MEMORY_SIZE)
            self.data.value = data
            log.debug("[%s][P: %s] write message of length %s", os.getpid(), os.getppid(), data_len)
            self.checksum = last_checksum

    @staticmethod
    def serialize(payload_list: Sequence[Payload]) -> bytes:
        return json.dumps(
            {"payload_list": [asdict(p) for p in payload_list], "shared_data_counter": time.monotonic_ns()},
            cls=UUIDEncoder,
            ensure_ascii=False,
        ).encode()
