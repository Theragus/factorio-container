#!/usr/bin/env python3
"""Minimal Source RCON client, used by the smoke test to talk to the server.

Usage: rcon.py <host> <port> <password> <command>
"""

from __future__ import annotations

import socket
import struct
import sys

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RconError(RuntimeError):
    pass


def _encode(request_id: int, packet_type: int, body: str) -> bytes:
    payload = (
        struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
    )
    return struct.pack("<i", len(payload)) + payload


def _read_exactly(sock: socket.socket, count: int) -> bytes:
    chunks = b""
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise RconError("connection closed by the server")
        chunks += chunk
    return chunks


def _read_packet(sock: socket.socket) -> tuple[int, int, str]:
    (length,) = struct.unpack("<i", _read_exactly(sock, 4))
    payload = _read_exactly(sock, length)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    return request_id, packet_type, payload[8:-2].decode("utf-8", errors="replace")


def execute(
    host: str, port: int, password: str, command: str, timeout: float = 30.0
) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)

        sock.sendall(_encode(1, SERVERDATA_AUTH, password))
        request_id, packet_type, _ = _read_packet(sock)
        # Some servers send an empty RESPONSE_VALUE before the auth response.
        if packet_type == SERVERDATA_RESPONSE_VALUE:
            request_id, packet_type, _ = _read_packet(sock)
        if request_id == -1:
            raise RconError("RCON authentication failed")
        if packet_type != SERVERDATA_AUTH_RESPONSE:
            raise RconError(f"unexpected packet type {packet_type} during auth")

        sock.sendall(_encode(2, SERVERDATA_EXECCOMMAND, command))
        _, _, body = _read_packet(sock)
        return body


def main() -> int:
    if len(sys.argv) < 5:
        print(__doc__, file=sys.stderr)
        return 2
    host, port, password = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    command = " ".join(sys.argv[4:])
    try:
        print(execute(host, port, password, command), end="")
    except (RconError, OSError) as error:
        print(f"rcon: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
