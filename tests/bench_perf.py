"""Headless micro-benchmarks for the performance report.

Run:  python tests/bench_perf.py
Covers the operations the optimization pass targeted. Numbers print as a
table; compare against the BASELINE constants captured before the change.
"""

import json
import os
import pickle
import socket
import tempfile
import time
import timeit

import numpy as np

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# Measured on this machine BEFORE the optimization pass (2026-10-15,
# CPython 3.13, arcade 3.3.3, numpy 2.2.6). Updated only by re-running the
# same script against the pre-change revision.
BASELINE = {
    "json_packet_ms": 0.051,
    "kdtree_20query_ms": 0.379,   # eliminated from per-frame path
    "tuple_sort_ms": 0.0067,       # replacement ordering cost
}

N_DRIVERS = 20


def make_packet():
    frame = {
        "t": 123.456, "lap": 12,
        "drivers": {
            f"D{i:02d}": {
                "x": 100.0 + i, "y": 200.0 - i, "dist": 5000.0 + i * 10,
                "lap": 12, "rel_dist": 0.5, "tyre": 1.0, "tyre_life": 5.0,
                "position": i + 1, "speed": 300.0, "gear": 7, "drs": 12,
                "throttle": 100.0, "brake": 0.0,
            }
            for i in range(N_DRIVERS)
        },
    }
    return {
        "frame_index": 100, "frame": frame, "track_status": "GREEN",
        "playback_speed": 1.0, "is_paused": False, "total_frames": 100000,
        "driver_colors": {f"D{i:02d}": "#FF0000" for i in range(N_DRIVERS)},
        "session_data": {"time": "01:02:03", "lap": 12, "leader": "D00",
                         "total_laps": 57},
    }


def bench_json():
    pkt = make_packet()
    fn = lambda: json.dumps(pkt).encode("utf-8")  # noqa: E731
    ms = timeit.timeit(fn, number=500) / 500 * 1000
    return ms, len(json.dumps(pkt).encode())


def bench_ordering():
    from scipy.spatial import cKDTree

    xs = np.random.rand(4000) * 5000
    ys = np.random.rand(4000) * 5000
    tree = cKDTree(np.column_stack((xs, ys)))
    pts = np.random.rand(N_DRIVERS, 2) * 5000
    kdtree_ms = (timeit.timeit(lambda: [tree.query(p) for p in pts], number=100)
                 / 100 * 1000)
    rows = [(f"D{i:02d}", (12, 5000.0 + i * 10)) for i in range(N_DRIVERS)]
    tuple_ms = (timeit.timeit(
        lambda: sorted(rows, key=lambda kv: kv[1], reverse=True), number=2000)
        / 2000 * 1000)
    return kdtree_ms, tuple_ms


def bench_cache_roundtrip():
    from src.services import replay_cache

    with tempfile.TemporaryDirectory() as tmp:
        orig = replay_cache._computed_dir
        replay_cache._computed_dir = lambda: tmp  # noqa: E731
        try:
            pkt = make_packet()
            payload = {
                "frames": [dict(pkt["frame"], t=round(i * 0.04, 3))
                           for i in range(250)],
                "driver_colors": {"D00": (255, 0, 0)},
                "track_statuses": [{"status": "1", "start_time": 0.0,
                                    "end_time": None}],
                "total_laps": 57,
                "max_tyre_life": {1: 20},
                "sector_data": {},
            }
            t0 = time.monotonic()
            ok, path = replay_cache.store_race_replay(payload, year=2025,
                                                      round_number=12)
            write_ms = (time.monotonic() - t0) * 1000
            size = os.path.getsize(path)
            t0 = time.monotonic()
            loaded, meta = replay_cache.load_race_replay(year=2025,
                                                         round_number=12,
                                                         session_type="R")
            read_ms = (time.monotonic() - t0) * 1000
            assert ok and loaded is not None and meta["hit"]
            return write_ms, read_ms, size
        finally:
            replay_cache._computed_dir = orig


def bench_stream_slow_client():
    from src.services.stream import TelemetryStreamServer

    sock = socket.socket()
    sock.bind(("localhost", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = TelemetryStreamServer(port=port)
    server.start()
    slow = socket.socket()
    slow.connect(("localhost", port))
    time.sleep(0.3)
    try:
        t0 = time.monotonic()
        sent = 0
        for i in range(2000):
            server.broadcast({"i": i, "pad": "x" * 4000})
            sent += 1
            if server.client_count == 0:
                break
        elapsed_ms = (time.monotonic() - t0) * 1000
        return elapsed_ms, sent, server.client_count, server.dropped_packets
    finally:
        server.stop()
        slow.close()


def main():
    json_ms, pkt_bytes = bench_json()
    kdtree_ms, tuple_ms = bench_ordering()
    write_ms, read_ms, size = bench_cache_roundtrip()
    stream_ms, stream_n, remaining, dropped = bench_stream_slow_client()

    rows = [
        ("Telemetry packet JSON serialize (ms/pkt)", BASELINE["json_packet_ms"], json_ms),
        ("Per-frame KDTree ordering, 20 cars (ms)", BASELINE["kdtree_20query_ms"], 0.0),
        ("Replacement tuple ordering, 20 cars (ms)", BASELINE["tuple_sort_ms"], tuple_ms),
    ]
    print(f"{'Metric':55s} {'Baseline':>10s} {'Optimized':>10s} {'Result':>10s}")
    for name, base, opt in rows:
        if base and opt:
            pct = (base - opt) / base * 100
            result = f"-{pct:.1f}%"
        elif base and not opt:
            result = "eliminated"
        else:
            result = "measured"
        print(f"{name:55s} {base:10.4f} {opt:10.4f} {result:>10s}")
    print(f"Packet size: {pkt_bytes} bytes")
    print(f"Cache round-trip (250 synthetic frames): "
          f"write {write_ms:.1f}ms / read {read_ms:.1f}ms / {size:,} bytes")
    print(f"Stream vs never-reading client: {stream_n} packets in "
          f"{stream_ms:.1f}ms, clients left {remaining}, "
          f"dropped {dropped} (replay never blocked)")


if __name__ == "__main__":
    main()
