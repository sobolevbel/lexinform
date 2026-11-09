"""Notify systemd once the running relay has confirmed its first poll."""

import socket


def notify_ready(address: str) -> None:
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.sendto(b"READY=1", address)
