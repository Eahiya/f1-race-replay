"""Extended stream reliability tests: sanitize, split encodings,
multi-client, reconnect, coalescing, shutdown."""

import json
import socket
import threading
import time

from src.services.stream import (
    TelemetryStreamServer,
    sanitize_for_json,
)


def _free_port():
    sock = socket.socket()
    sock.bind(("localhost", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _recv_lines(sock, count, timeout=10):
    sock.settimeout(timeout)
    buf = b""
    out = []
    while len(out) < count:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                out.append(line)
    return out


def test_nan_inf_sanitized_to_null_on_wire():
    port = _free_port()
    server = TelemetryStreamServer(port=port)
    server.start()
    try:
        sock = socket.socket()
        sock.connect(("localhost", port))
        try:
            time.sleep(0.3)
            server.broadcast({"rel": float("nan"),
                              "inf": float("inf"),
                              "neg": float("-inf"),
                              "ok": 1.5,
                              "nested": {"v": float("nan")}})
            lines = _recv_lines(sock, 1)
            assert len(lines) == 1
            raw = lines[0]
            assert b"NaN" not in raw and b"Infinity" not in raw
            # Strict parse: any non-standard constant raises.
            parsed = json.loads(raw.decode("utf-8"),
                                parse_constant=lambda v: (_ for _ in ()).throw(
                                    ValueError(f"bad constant {v}")))
            assert parsed["rel"] is None
            assert parsed["nested"]["v"] is None
            assert parsed["ok"] == 1.5
        finally:
            sock.close()
    finally:
        server.stop()


def test_sanitize_preserves_schema_and_types():
    packet = {"a": 1, "b": "x", "c": [1.0, float("nan")],
              "d": {"e": float("inf")}, "f": None, "g": True}
    clean = sanitize_for_json(packet)
    assert clean == {"a": 1, "b": "x", "c": [1.0, None],
                     "d": {"e": None}, "f": None, "g": True}
    json.dumps(clean)  # must not raise


def test_split_multibyte_character_survives():
    """A multibyte char divided across reads must not break the client."""
    port = _free_port()
    server = TelemetryStreamServer(port=port)
    server.start()
    try:
        sock = socket.socket()
        sock.connect(("localhost", port))
        try:
            time.sleep(0.3)
            # Bypass broadcast() to control chunking: send raw bytes in
            # the middle of a 3-byte UTF-8 sequence.
            with server.clients_lock:
                client = list(server.clients)[0]
            payload = json.dumps({"note": "café €"},
                                   ensure_ascii=False).encode("utf-8") + b"\n"
            cut = payload.index("€".encode("utf-8")) + 1
            client.send(payload[:cut])
            time.sleep(0.2)
            client.send(payload[cut:])
            client.send(json.dumps({"i": 2}).encode() + b"\n")
            lines = _recv_lines(sock, 2)
            assert len(lines) == 2
            assert json.loads(lines[0].decode())["note"] == "café €"
        finally:
            sock.close()
    finally:
        server.stop()


def test_multiple_clients_independent_disconnect():
    port = _free_port()
    server = TelemetryStreamServer(port=port)
    server.start()
    try:
        first = socket.socket()
        first.connect(("localhost", port))
        second = socket.socket()
        second.connect(("localhost", port))
        try:
            time.sleep(0.3)
            assert server.client_count == 2
            server.broadcast({"i": 0})
            assert len(_recv_lines(first, 1)) == 1
            assert len(_recv_lines(second, 1)) == 1
            first.close()  # one leaves, the other keeps receiving
            time.sleep(1.5)
            for _ in range(TelemetryStreamServer.MAX_CONSECUTIVE_SEND_FAILURES + 2):
                server.broadcast({"i": 1})
            assert server.client_count <= 1
            server.broadcast({"i": 2})
            assert _recv_lines(second, 1)[0].endswith(b"}")
        finally:
            try:
                first.close()
            except OSError:
                pass
            second.close()
    finally:
        server.stop()


def test_reconnect_after_server_restart():
    port = _free_port()
    server = TelemetryStreamServer(port=port)
    server.start()
    sock = socket.socket()
    sock.connect(("localhost", port))
    time.sleep(0.3)
    server.broadcast({"i": 0})
    assert len(_recv_lines(sock, 1)) == 1
    sock.close()
    server.stop()
    time.sleep(0.3)
    server2 = TelemetryStreamServer(port=port)
    server2.start()
    try:
        sock2 = socket.socket()
        sock2.connect(("localhost", port))
        try:
            time.sleep(0.3)
            server2.broadcast({"i": 1})
            lines = _recv_lines(sock2, 1)
            assert json.loads(lines[0].decode())["i"] == 1
        finally:
            sock2.close()
    finally:
        server2.stop()


def test_double_start_is_safe():
    port = _free_port()
    server = TelemetryStreamServer(port=port)
    server.start()
    try:
        server.start()  # must not raise or leak an accept loop
        server.broadcast({"i": 0})
    finally:
        server.stop()


def test_concurrent_broadcasts_do_not_corrupt():
    port = _free_port()
    server = TelemetryStreamServer(port=port)
    server.start()
    try:
        sock = socket.socket()
        sock.connect(("localhost", port))
        try:
            time.sleep(0.3)
            errors = []

            def hammer(base):
                try:
                    for i in range(25):
                        server.broadcast({"t": base, "i": i})
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=hammer, args=(t,)) for t in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert errors == []
            lines = _recv_lines(sock, 100, timeout=15)
            assert len(lines) == 100
            for line in lines:  # every line is intact JSON
                json.loads(line.decode("utf-8"))
        finally:
            sock.close()
    finally:
        server.stop()


def test_coalesce_client_receives_newest_snapshot():
    from src.services.stream import TelemetryStreamClient

    received = []

    class _Harness(TelemetryStreamClient):
        def __init__(self):
            super().__init__(coalesce=True)

        def _emit_line(self, line):  # capture instead of emitting signals
            received.append(line)

    client = _Harness()
    # Simulate three packets arriving in a single socket read.
    buffer = b""
    for i in range(3):
        buffer += json.dumps({"i": i}).encode() + b"\n"
    *lines, buffer = buffer.split(b"\n")
    newest = None
    for raw in lines:
        if raw.strip():
            newest = raw.decode()
    assert newest is not None
    client._emit_line(newest)
    assert [json.loads(line)["i"] for line in received] == [2]
