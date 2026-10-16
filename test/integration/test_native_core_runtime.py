from __future__ import annotations

import gc
import json
import os
import socket
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest


class MockRuntimeServer:
    def __init__(self, *, policy_delay_ms: int = 0, policy_payload: bytes = b"\x08\x01") -> None:
        self._policy_delay_ms = policy_delay_ms
        # CheckActionResponse protobuf for the PolicyQuery reply. Default is
        # decision=ALLOW (\x08\x01); pass \x08\x02 for DENY, etc.
        self._policy_payload = policy_payload
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._socket_dir = tempfile.TemporaryDirectory(prefix="aaasm55-")
        self.socket_path = str(Path(self._socket_dir.name) / "runtime.sock")

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("mock runtime server did not start")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._socket_dir.cleanup()

    def _run(self) -> None:
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(self.socket_path)
            server.listen(1)
            self._ready.set()

            conn, _ = server.accept()
            with conn:
                conn.settimeout(0.2)

                # AAASM-3587: the real runtime issues a handshake challenge first
                # and the shared client completes it (fail-closed) before any
                # other traffic. The mock needs only a decodable challenge
                # (HandshakeChallenge { nonce: bytes } = field 1, 32 bytes); it
                # does not verify the proof — that is the runtime's job.
                self._write_frame(conn, 5, b"\x0a\x20" + b"\x5a" * 32)

                while not self._stop.is_set():
                    try:
                        tag = self._read_u8(conn)
                    except TimeoutError:
                        continue
                    except OSError:
                        return

                    if tag == 4:
                        # Heartbeat frame from client is a single tag byte.
                        try:
                            self._write_frame(conn, 3, b"")
                        except OSError:
                            return
                        continue

                    if tag in (1, 2, 3, 5):
                        _ = self._read_length_delimited(conn)

                    if tag == 5:
                        continue
                    if tag == 1:
                        if self._policy_delay_ms > 0:
                            time.sleep(self._policy_delay_ms / 1000.0)
                        # CheckActionResponse protobuf payload (decision field).
                        try:
                            self._write_frame(conn, 1, self._policy_payload)
                        except OSError:
                            return
                    elif tag == 2 or tag == 3:
                        try:
                            self._write_frame(conn, 3, b"")
                        except OSError:
                            return
                    else:
                        return

    @staticmethod
    def _read_u8(conn: socket.socket) -> int:
        chunk = conn.recv(1)
        if not chunk:
            raise OSError("socket closed")
        return chunk[0]

    @staticmethod
    def _read_varint(conn: socket.socket) -> int:
        result = 0
        shift = 0
        while True:
            byte = MockRuntimeServer._read_u8(conn)
            result |= (byte & 0x7F) << shift
            if byte & 0x80 == 0:
                return result
            shift += 7
            if shift >= 64:
                raise ValueError("varint too long")

    @staticmethod
    def _read_exact(conn: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = conn.recv(size - len(data))
            if not chunk:
                raise OSError("socket closed")
            data += chunk
        return data

    @classmethod
    def _read_length_delimited(cls, conn: socket.socket) -> bytes:
        size = cls._read_varint(conn)
        return cls._read_exact(conn, size)

    @staticmethod
    def _write_varint(conn: socket.socket, value: int) -> None:
        while True:
            byte = value & 0x7F
            value >>= 7
            if value == 0:
                conn.sendall(bytes([byte]))
                return
            conn.sendall(bytes([byte | 0x80]))

    @classmethod
    def _write_frame(cls, conn: socket.socket, tag: int, payload: bytes) -> None:
        conn.sendall(bytes([tag]))
        cls._write_varint(conn, len(payload))
        if payload:
            conn.sendall(payload)


def make_audit_entry_payload(index: int, *, worker_id: int = 0) -> str:
    return json.dumps(
        {
            "seq": index,
            "timestamp_ns": 1_700_000_000_000_000_000 + index,
            "event_type": "ToolCallIntercepted",
            "agent_id": [worker_id % 255] * 16,
            "session_id": [index % 255] * 16,
            "payload": json.dumps({"index": index, "worker": worker_id}),
            "previous_hash": [0] * 32,
            "entry_hash": [0] * 32,
        }
    )


@pytest.fixture
def native_core() -> Any:
    if os.getenv("AAASM_RUN_NATIVE_CORE_TESTS") != "1":
        pytest.skip("Set AAASM_RUN_NATIVE_CORE_TESTS=1 to run native core runtime tests.")
    return pytest.importorskip("agent_assembly._core")


@pytest.mark.integration
def test_send_event_is_non_blocking(native_core: Any) -> None:
    server = MockRuntimeServer()
    server.start()

    client = native_core.RuntimeClient.connect(server.socket_path)
    try:
        events = [native_core.GovernanceEvent(make_audit_entry_payload(index)) for index in range(500)]
        start = time.perf_counter()
        for event in events:
            client.send_event(event)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert elapsed_ms < 50.0
    finally:
        client.close()
        server.close()


@pytest.mark.integration
def test_runtime_client_has_no_thread_deadlock(native_core: Any) -> None:
    server = MockRuntimeServer()
    server.start()

    client = native_core.RuntimeClient.connect(server.socket_path)
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            for index in range(100):
                client.send_event(native_core.GovernanceEvent(make_audit_entry_payload(index, worker_id=worker_id)))
        except Exception as error:  # pragma: no cover - runtime guard
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(worker_id,)) for worker_id in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    try:
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
    finally:
        client.close()
        server.close()


@pytest.mark.integration
def test_runtime_client_tracemalloc_leak_guard(native_core: Any) -> None:
    server = MockRuntimeServer()
    server.start()

    client = native_core.RuntimeClient.connect(server.socket_path)
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    try:
        for index in range(10_000):
            client.send_event(native_core.GovernanceEvent(make_audit_entry_payload(index)))
        gc.collect()
        current, _ = tracemalloc.get_traced_memory()
        assert current - baseline_current < 1_000_000
    finally:
        tracemalloc.stop()
        client.close()
        server.close()


@pytest.mark.integration
def test_query_policy_returns_deny_when_runtime_denies(native_core: Any) -> None:
    # CheckActionResponse { decision: DENY } => field 1 (varint) = 2.
    server = MockRuntimeServer(policy_payload=b"\x08\x02")
    server.start()

    client = native_core.RuntimeClient.connect(server.socket_path)
    try:
        result = client.query_policy("agent-001", "tool_call", "web_search", '{"q": "x"}')
        assert result["decision"] == "deny"
    finally:
        client.close()
        server.close()


def _redact_check(native_core: Any, payload: bytes, *, enforce: bool) -> dict[str, Any]:
    """Drive the REAL native client + production interceptor against a mock runtime."""
    from agent_assembly.core.runtime_interceptor import RuntimeQueryInterceptor

    server = MockRuntimeServer(policy_payload=payload)
    server.start()
    client = native_core.RuntimeClient.connect(server.socket_path)
    try:
        interceptor = RuntimeQueryInterceptor(object(), client, "agent-001", enforce=enforce)
        return interceptor.check_tool_start(serialized={"name": "web_search"}, input_str="query")
    finally:
        client.close()
        server.close()


@pytest.mark.integration
def test_native_surfaces_a_runtime_redact_verdict(native_core: Any) -> None:
    # CheckActionResponse { decision: REDACT } => field 1 (varint) = 4.
    server = MockRuntimeServer(policy_payload=b"\x08\x04")
    server.start()
    client = native_core.RuntimeClient.connect(server.socket_path)
    try:
        result = client.query_policy("agent-001", "tool_call", "web_search", '{"q": "x"}')
        assert result["decision"] == "redact"
    finally:
        client.close()
        server.close()


@pytest.mark.integration
def test_real_native_redact_denies_under_enforce(native_core: Any) -> None:
    """AAASM-6352: a real runtime REDACT must not run the tool with the original args."""
    result = _redact_check(native_core, b"\x08\x04", enforce=True)
    assert result["status"] == "deny"
    assert "cannot apply client-side" in result["reason"]


@pytest.mark.integration
def test_real_native_allow_proceeds_under_enforce_control(native_core: Any) -> None:
    """Paired control: same path and posture, only the runtime verdict differs."""
    assert _redact_check(native_core, b"\x08\x01", enforce=True) == {"status": "allow"}


@pytest.mark.integration
def test_real_native_redact_stays_advisory_without_enforce(native_core: Any) -> None:
    assert _redact_check(native_core, b"\x08\x04", enforce=False) == {"status": "allow"}


@pytest.mark.integration
def test_query_policy_fails_open_when_runtime_unreachable(native_core: Any) -> None:
    # No server bound at this path: the runtime never answers, so the shared
    # client's query times out and the binding must fail OPEN (allow), never
    # surface an error a caller would read as a deny.
    socket_dir = tempfile.TemporaryDirectory(prefix="aaasm3046-")
    socket_path = str(Path(socket_dir.name) / "absent.sock")

    client = native_core.RuntimeClient.connect(socket_path)
    try:
        result = client.query_policy("agent-001", "tool_call")
        assert result["decision"] == "allow"
    finally:
        client.close()
        socket_dir.cleanup()


@pytest.mark.integration
def test_wired_check_blocks_on_runtime_deny(native_core: Any) -> None:
    """Wave 3 (AAASM-3049): a real runtime DENY drives on_tool_start to raise.

    Connects a native RuntimeClient to a mock-UDS runtime answering DENY, wraps
    it in the production RuntimeQueryInterceptor, and confirms the LangChain
    callback handler blocks the tool with ToolExecutionBlockedError.
    """
    from uuid import uuid4

    from agent_assembly.adapters.langchain import AssemblyCallbackHandler
    from agent_assembly.core.runtime_interceptor import RuntimeQueryInterceptor
    from agent_assembly.exceptions import ToolExecutionBlockedError

    server = MockRuntimeServer(policy_payload=b"\x08\x02")
    server.start()

    client = native_core.RuntimeClient.connect(server.socket_path)
    try:
        interceptor = RuntimeQueryInterceptor(object(), client, "agent-001")
        handler = AssemblyCallbackHandler(interceptor)
        run_id = uuid4()
        with pytest.raises(ToolExecutionBlockedError):
            handler.on_tool_start(
                serialized={"name": "web_search"},
                input_str="query",
                run_id=run_id,
                tool_name="web_search",
                args={"q": "x"},
            )
    finally:
        client.close()
        server.close()


@pytest.mark.integration
def test_wired_check_fails_open_when_runtime_unreachable(native_core: Any) -> None:
    """Wave 3 (AAASM-3049): no reachable runtime => on_tool_start proceeds.

    The native query_policy fails open (allow) when the socket is unbound, so the
    wired check must NOT raise.
    """
    from uuid import uuid4

    from agent_assembly.adapters.langchain import AssemblyCallbackHandler
    from agent_assembly.core.runtime_interceptor import RuntimeQueryInterceptor

    socket_dir = tempfile.TemporaryDirectory(prefix="aaasm3049-")
    socket_path = str(Path(socket_dir.name) / "absent.sock")

    client = native_core.RuntimeClient.connect(socket_path)
    try:
        interceptor = RuntimeQueryInterceptor(object(), client, "agent-001")
        handler = AssemblyCallbackHandler(interceptor)
        # Must not raise: fail-open.
        handler.on_tool_start(
            serialized={"name": "web_search"},
            input_str="query",
            run_id=uuid4(),
            tool_name="web_search",
            args={"q": "x"},
        )
    finally:
        client.close()
        socket_dir.cleanup()


@pytest.mark.integration
def test_the_audit_payload_builder_is_accepted_by_the_real_governance_event(native_core: Any) -> None:
    """The SDK's audit payload must satisfy the REAL native constructor (AAASM-5750).

    ``GovernanceEvent.__new__`` deserializes its argument as
    ``aa_core::AuditEntry`` JSON and raises ``ValueError`` on anything else, so a
    builder emitting the wrong shape produces no record at all — silently, since
    ``send_tool_outcome`` swallows the failure to keep a degraded audit channel
    from breaking a governed tool call.

    The unit suite substitutes a double for this constructor and can therefore
    only catch an obviously wrong payload; it cannot establish that the real one
    accepts a given payload. This test is where that is established, so it lives
    behind the same native gate as the rest of this module.
    """
    from agent_assembly.core.runtime_audit import build_tool_outcome_payload

    for denied in (False, True):
        payload = build_tool_outcome_payload(
            tool_name="web_search",
            result="RESULT" if not denied else "blocked by policy",
            agent_id="agent-1",
            run_id="run-1",
            denied=denied,
        )
        # Constructing it IS the assertion: a rejected payload raises ValueError.
        event = native_core.GovernanceEvent(payload)
        assert event.payload_json == payload

    # Negative control on the same constructor: it must reject a payload that is
    # not an AuditEntry, or "it accepted ours" would say nothing.
    with pytest.raises(ValueError):
        native_core.GovernanceEvent(json.dumps({"event_type": "ToolCallIntercepted"}))
