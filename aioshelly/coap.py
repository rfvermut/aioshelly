"""COAP for Shelly."""
import asyncio
import json
import logging
import socket
import struct
from typing import Optional, cast

_LOGGER = logging.getLogger(__name__)


class CoapError(Exception):
    """Base class for COAP errors."""


class InvalidMessage(CoapError):
    """Raised during COAP message parsing errors."""


class CoapMessage:
    """Represents a received coap message."""

    def __init__(self, sender_addr, payload: bytes):
        """Initialize a coap message."""
        self.ip = sender_addr[0]
        self.port = sender_addr[1]

        try:
            self.vttkl, self.code, self.mid = struct.unpack("!BBH", payload[:4])
        except struct.error as err:
            raise InvalidMessage("Message too short") from err

        if self.code not in (30, 69):
            raise InvalidMessage(f"Wrong type, {self.code}")

        try:
            self.payload = json.loads(payload.rsplit(b"\xff", 1)[1].decode())
        except (json.decoder.JSONDecodeError, IndexError) as err:
            raise InvalidMessage(
                f"Message type {self.code} is not a valid JSON format: {str(payload)}"
            ) from err

        if self.code == 30:
            coap_type = "cit/s"
        else:
            coap_type = "cit/d"
        _LOGGER.debug(
            "CoapMessage: ip=%s, type=%s, payload=%s", self.ip, coap_type, self.payload
        )


def socket_init():
    """Init UDP socket to send/receive data with Shelly devices."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 5683))
    mreq = struct.pack("=4sl", socket.inet_aton("224.0.1.187"), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)
    return sock


class COAP(asyncio.DatagramProtocol):
    """COAP manager."""

    def __init__(self, message_received=None):
        """Initialize COAP manager."""
        self.sock = None
        # Will receive all updates
        self._message_received = message_received
        self.subscriptions = {}
        self.transport: Optional[asyncio.DatagramTransport] = None

    async def initialize(self):
        """Initialize the COAP manager."""
        loop = asyncio.get_running_loop()
        self.sock = socket_init()
        await loop.create_datagram_endpoint(lambda: self, sock=self.sock)

    async def request(self, ip: str, path: str):
        """Request a CoAP message.

        Subscribe with `subscribe_updates` to receive answer.
        """
        assert self.transport is not None
        msg = b"\x50\x01\x00\x0A\xb3cit\x01" + path.encode() + b"\xFF"
        _LOGGER.debug("Sending request 'cit/%s' to device %s", path, ip)
        self.transport.sendto(msg, (ip, 5683))

    def close(self):
        """Close."""
        self.transport.close()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """When the socket is set up."""
        self.transport = cast(asyncio.DatagramTransport, transport)

    def datagram_received(self, data, addr):
        """Handle incoming datagram messages."""
        host_ip = addr[0]
        try:
            msg = CoapMessage(addr, data)
        except InvalidMessage as err:
            if host_ip in self.subscriptions:
                _LOGGER.error("Invalid Message from known host %s: %s", host_ip, err)
            else:
                _LOGGER.debug("Invalid Message from unknown host %s: %s", host_ip, err)
            return

        if self._message_received:
            self._message_received(msg)

        if msg.ip in self.subscriptions:
            _LOGGER.debug("Adding CoAP message to subscriptions for device %s", msg.ip)
            self.subscriptions[msg.ip](msg)

    def subscribe_updates(self, ip, message_received):
        """Subscribe to received updates."""
        _LOGGER.debug("Adding CoAP message to subscriptions for device %s", ip)
        self.subscriptions[ip] = message_received
        return lambda: self.subscriptions.pop(ip)

    async def __aenter__(self):
        """Entering async context manager."""
        await self.initialize()
        return self

    async def __aexit__(self, _type, _value, _traceback):
        """Leaving async context manager."""
        self.close()


async def discovery_dump():
    """Dump all discovery data as it comes in."""
    async with COAP(lambda msg: print(msg.ip, msg.payload)):
        while True:
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    try:
        asyncio.run(discovery_dump())
    except KeyboardInterrupt:
        pass
