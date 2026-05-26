from __future__ import annotations

import os
import socket
import time


class SystemdWatchdog:
    def __init__(self) -> None:
        self.socket_path = os.environ.get("NOTIFY_SOCKET")
        self.interval = self._interval()
        self.next_ping = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.socket_path)

    def ready(self) -> None:
        self._notify("READY=1")

    def ping(self) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if now < self.next_ping:
            return
        self._notify("WATCHDOG=1")
        self.next_ping = now + self.interval

    def stopping(self) -> None:
        self._notify("STOPPING=1")

    def _interval(self) -> float:
        try:
            usec = int(os.environ.get("WATCHDOG_USEC", "0") or "0")
        except ValueError:
            usec = 0
        if usec <= 0:
            return 10.0
        return max(1.0, usec / 2_000_000.0)

    def _notify(self, message: str) -> None:
        if not self.socket_path:
            return
        address: str | bytes = self.socket_path
        if address.startswith("@"):
            address = b"\0" + address[1:].encode()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(address)
                sock.sendall(message.encode())
        except OSError:
            pass
