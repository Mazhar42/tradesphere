"""Fetching a file from an address a dealership typed in, without letting that address reach
anything private: HTTPS only, every resolved address must be public, the connection is made to
the address that was checked (so the name cannot change in between), and each redirect is
checked again."""

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

from app.core.errors import BusinessRuleError
from app.modules.imports.parsing import MAX_BYTES

CONNECT_SECONDS = 10
TOTAL_SECONDS = 60
REDIRECTS = 3
NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _refuse(message: str, code: str = "unsafe_address") -> BusinessRuleError:
    return BusinessRuleError(message, code=code)


def check_url(url: str) -> tuple[str, str]:
    """(host, path with query) of an acceptable address."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise _refuse("That is not a web address.") from error
    if parts.scheme != "https" or not parts.hostname or port not in (None, 443):
        raise _refuse("Only https:// addresses on the standard port are allowed.")
    if parts.username or parts.password:
        raise _refuse("Put the password in the credentials field, not in the address.")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise _refuse("That is not a web address.")
    return parts.hostname, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def is_public(text: str) -> bool:
    address = ipaddress.ip_address(text.split("%", maxsplit=1)[0])
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            address = address.ipv4_mapped
        elif address.sixtofour or address.teredo or address in NAT64:
            return False
    return address.is_global and not address.is_multicast


def resolve(host: str) -> str:
    try:
        found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as error:
        raise _refuse("The address could not be found.", "fetch_failed") from error
    addresses = [str(entry[4][0]) for entry in found]
    if not addresses or not all(is_public(a) for a in addresses):
        raise _refuse("That address is not on the public internet.")
    return addresses[0]


class _Pinned(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        self.tls = ssl.create_default_context()
        super().__init__(host, 443, timeout=CONNECT_SECONDS, context=self.tls)
        self.address = address

    def connect(self) -> None:
        raw = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self.tls.wrap_socket(raw, server_hostname=self.host)


def fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    deadline = time.monotonic() + TOTAL_SECONDS
    first_host = check_url(url)[0]
    for _ in range(REDIRECTS + 1):
        host, path = check_url(url)
        connection = _Pinned(host, resolve(host))
        try:
            send = {"Accept-Encoding": "identity", "User-Agent": "TradeSphere-sync"}
            if host == first_host:  # credentials never follow a redirect elsewhere
                send.update(headers or {})
            connection.request("GET", path, headers=send)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get("Location", ""))
                continue
            if response.status != 200:
                raise _refuse(f"The server answered {response.status}.", "fetch_failed")
            body = bytearray()
            while chunk := response.read(64 * 1024):
                body += chunk
                if len(body) > MAX_BYTES:
                    raise _refuse("The file is larger than 10 MB.", "fetch_too_large")
                if time.monotonic() > deadline:
                    raise _refuse("The server was too slow.", "fetch_failed")
            return bytes(body)
        except (OSError, http.client.HTTPException, ValueError) as error:
            # The type only: messages can contain the address and what was sent to it.
            raise _refuse(
                f"Could not fetch the file ({type(error).__name__}).", "fetch_failed"
            ) from error
        finally:
            connection.close()
    raise _refuse("Too many redirects.", "fetch_failed")
