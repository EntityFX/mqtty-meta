"""Bounded known-count MQTT 3.1.1 meter check against an OWN Mosquitto process.

Not a broker benchmark, not MqttBenchmark, and not a native/model accuracy test.
Uses a small independent protocol client to check IDs, QoS handshakes and phases.
"""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import threading
import time

from audit_twin_cohort import file_digest, recount


def packet(header, body=b""):
    length, encoded = len(body), bytearray()
    while True:
        digit = length % 128
        length //= 128
        encoded.append(digit | (128 if length else 0))
        if not length:
            return bytes([header]) + encoded + body


def text(value):
    value = value.encode()
    return struct.pack("!H", len(value)) + value


def exact(sock, count):
    value = bytearray()
    while len(value) < count:
        part = sock.recv(count - len(value))
        if not part:
            raise RuntimeError("MQTT peer disconnected")
        value.extend(part)
    return bytes(value)


def receive(sock):
    header = exact(sock, 1)[0]
    length, multiplier = 0, 1
    for _ in range(4):
        digit = exact(sock, 1)[0]
        length += (digit & 127) * multiplier
        if not digit & 128:
            if length > 1024 * 1024:
                raise ValueError("Unexpectedly large packet")
            return header, exact(sock, length)
        multiplier *= 128
    raise ValueError("Invalid MQTT remaining length")


def connect(port, identifier):
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.settimeout(3)
    sock.sendall(packet(0x10, text("MQTT") + b"\x04\x02\x00\x1e" + text(identifier)))
    if receive(sock) != (0x20, b"\0\0"):
        sock.close()
        raise RuntimeError("CONNACK was not successful")
    return sock


def trial(port, qos, root):
    root.mkdir()
    origin = time.monotonic()
    now = lambda: time.monotonic() - origin
    publications, arrivals, errors, pending = [], [], [], {}
    stop = threading.Event()
    subscriber = connect(port, f"twin-micro-s{qos}")
    publisher = connect(port, f"twin-micro-p{qos}")
    topic = f"twin-micro/q{qos}"
    subscriber.sendall(packet(0x82, b"\0\1" + text(topic) + bytes([qos])))
    if receive(subscriber) != (0x90, bytes([0, 1, qos])):
        raise RuntimeError("Unexpected SUBACK")

    def record(payload):
        arrivals.append(dict(id=payload.decode("ascii"), elapsedSeconds=now(), payloadBytes=len(payload)))

    def consume():
        try:
            while not stop.is_set():
                if not select.select([subscriber], [], [], .1)[0]:
                    continue
                header, body = receive(subscriber)
                if header >> 4 == 3:
                    size = struct.unpack("!H", body[:2])[0]
                    received_topic = body[2:2 + size].decode()
                    if received_topic != topic or ((header >> 1) & 3) != qos:
                        raise ValueError("Unexpected topic or delivery QoS")
                    pid = body[2 + size:4 + size]
                    payload = body[4 + size:]
                    if qos == 1:
                        record(payload)
                        subscriber.sendall(packet(0x40, pid))
                    else:
                        pending[pid] = payload
                        subscriber.sendall(packet(0x50, pid))
                elif header == 0x62 and qos == 2:
                    record(pending.pop(body))
                    subscriber.sendall(packet(0x70, body))
                else:
                    raise ValueError(f"Unexpected subscriber packet {header}")
        except Exception as error:
            if not stop.is_set():
                errors.append(repr(error))

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    start = now()
    end = start + 2
    try:
        for index in range(100):
            time.sleep(max(0, start + index * .02 - now()))
            if now() >= end:
                break
            payload = f"{qos:x}{index:015x}".encode("ascii")
            identifier = payload.decode()
            pid = struct.pack("!H", index + 1)
            attempt = now()
            publications.append(dict(event="attempt", id=identifier, elapsedSeconds=attempt,
                                     publisher=0, sequence=index, payloadBytes=16))
            try:
                publisher.sendall(packet(0x30 | (qos << 1), text(topic) + pid + payload))
                header, body = receive(publisher)
                if (header, body) != (0x40 if qos == 1 else 0x50, pid):
                    raise ValueError("Unexpected publication acknowledgement")
                if qos == 2:
                    publisher.sendall(packet(0x62, pid))
                    if receive(publisher) != (0x70, pid):
                        raise ValueError("Expected PUBCOMP")
                publications.append(dict(event="completed", id=identifier, elapsedSeconds=now(),
                                         latencyMs=(now() - attempt) * 1000))
            except Exception as error:
                publications.append(dict(event="failed", id=identifier, elapsedSeconds=now(), error=repr(error)))
                break
        time.sleep(max(0, end - now()))
        deadline = time.monotonic() + 2
        while len(arrivals) < 100 and time.monotonic() < deadline and not errors:
            stop.wait(.01)
        drain = now()
    finally:
        stop.set()
        thread.join(timeout=3)
        publisher.close()
        subscriber.close()
    phases = dict(timeOrigin="sharedMonotonic", qos=qos, measurementStartSeconds=start,
                  measurementEndSeconds=end, drainEndSeconds=drain)
    for name, rows in (("publishes.ndjson", publications), ("deliveries.ndjson", arrivals)):
        with (root / name).open("x", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row) + "\n")
    (root / "phases.json").write_text(json.dumps(phases, indent=2), encoding="utf-8")
    result = recount(publications, arrivals, phases)
    result.update(expectedPublications=100, subscriberErrors=errors, pendingQos2Handshakes=len(pending),
                  payloadSizes=sorted({row["payloadBytes"] for row in arrivals}))
    result["inputsSha256"] = {name: file_digest(root / name) for name in
                             ("publishes.ndjson", "deliveries.ndjson", "phases.json")}
    result["knownAnswerPassed"] = (result["attempted"] == result["completed"] == result["completedIdsDelivered"] == 100
                                   and result["uniqueDeliveries"] == 100 and not result["duplicates"]
                                   and not result["pending"] and not errors and not pending and result["payloadSizes"] == [16])
    (root / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--broker", default="/usr/sbin/mosquitto")
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = root / "mosquitto.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n", encoding="utf-8")
    started = time.monotonic()
    results = []
    with (root / "broker.log").open("x") as log:
        broker = subprocess.Popen([args.broker, "-c", str(config)], stdout=log, stderr=subprocess.STDOUT)
        try:
            ready = False
            for _ in range(50):
                if broker.poll() is not None:
                    raise RuntimeError(f"Owned broker exited: {broker.returncode}")
                try:
                    probe = socket.create_connection(("127.0.0.1", port), timeout=.1)
                    probe.close()
                    ready = True
                    break
                except OSError:
                    time.sleep(.05)
            if not ready:
                raise RuntimeError("Owned broker failed to start")
            for qos in (1, 2):
                results.append(trial(port, qos, root / f"qos{qos}"))
        finally:
            broker.terminate()
            try:
                broker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                broker.kill()
                broker.wait(timeout=3)
    summary = dict(scope="Technical known-count check with an independent small MQTT client; not model accuracy",
                   brokerPath=args.broker, brokerSha256=file_digest(args.broker),
                   brokerPid=broker.pid, brokerExitCode=broker.returncode, port=port,
                   elapsedWallSeconds=time.monotonic() - started,
                   passed=all(result["knownAnswerPassed"] for result in results),
                   trials=[dict(qos=qos, **result) for qos, result in zip((1, 2), results)])
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
