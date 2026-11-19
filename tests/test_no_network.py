"""The suite's network guard (see ``tests/conftest.py``): every route off this machine is refused before a packet
leaves, and all of 127.0.0.0/8 and ``::1`` stay usable for tests that start a local server."""

import asyncio
import contextlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer

import pytest

OFF = ("192.0.2.1", 443)  # TEST-NET-1: never routable


def _refused(no_network, expected):
    assert no_network == [expected]
    no_network.clear()  # refused as intended, so the teardown doesn't fail the test


def test_a_connection_off_this_machine_is_refused(no_network):
    with pytest.raises(OSError, match="doesn't use the network"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)
    assert no_network == [("192.0.2.1", 443)]
    no_network.clear()  # refused as intended, so the teardown doesn't fail the test


def test_connect_ex_off_this_machine_is_refused(no_network):
    with socket.socket() as s, pytest.raises(OSError, match="doesn't use the network"):
        s.connect_ex(OFF)
    _refused(no_network, OFF)


def test_an_ipv6_connection_off_this_machine_is_refused(no_network):
    address = ("2001:db8::1", 443, 0, 0)
    with socket.socket(socket.AF_INET6) as s, pytest.raises(OSError, match="doesn't use the network"):
        s.connect(address)
    _refused(no_network, address)


@pytest.mark.parametrize("lookup", ["getaddrinfo", "gethostbyname", "gethostbyname_ex"])
def test_a_name_lookup_of_a_name_off_this_machine_is_refused(no_network, lookup):
    args = ("example.com", 80) if lookup == "getaddrinfo" else ("example.com",)
    with pytest.raises(OSError, match="doesn't use the network"):
        getattr(socket, lookup)(*args)
    assert [r[0] for r in no_network] == ["example.com"]
    no_network.clear()


def test_a_name_lookup_by_create_connection_is_refused_before_any_connect(no_network):
    with pytest.raises(OSError, match="doesn't use the network"):
        socket.create_connection(("example.com", 443), timeout=1)
    assert [r[0] for r in no_network] == ["example.com"]
    no_network.clear()


@pytest.mark.parametrize("host", ["192.0.2.1", "example.com"])
def test_an_asyncio_connection_off_this_machine_is_refused(no_network, host):
    async def go():
        await asyncio.open_connection(host, 443)

    with pytest.raises(OSError, match="doesn't use the network"):
        asyncio.run(go())
    assert [r[0] for r in no_network] == [host]
    no_network.clear()


@pytest.mark.parametrize("loop_class", ["SelectorEventLoop", "ProactorEventLoop"])
def test_every_asyncio_loop_refuses_a_connection_off_this_machine(no_network, loop_class):
    if not hasattr(asyncio, loop_class):
        pytest.skip(f"no {loop_class} on this platform")
    loop = getattr(asyncio, loop_class)()
    try:
        with pytest.raises(OSError, match="doesn't use the network"):
            loop.run_until_complete(asyncio.open_connection("192.0.2.1", 443))
    finally:
        loop.close()
    _refused(no_network, OFF)


def test_a_raw_asyncio_sock_connect_off_this_machine_is_refused(no_network):
    async def go():
        sock = socket.socket()
        sock.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_connect(sock, OFF)
        finally:
            sock.close()

    with pytest.raises(OSError, match="doesn't use the network"):
        asyncio.run(go())
    _refused(no_network, OFF)


def test_curl_cffi_off_this_machine_is_refused(no_network):
    requests = pytest.importorskip("curl_cffi.requests")
    with pytest.raises(OSError, match="doesn't use the network"):
        requests.Session().get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL")
    _refused(no_network, "https://query1.finance.yahoo.com/v8/finance/chart/AAPL")


def test_curl_cffi_async_off_this_machine_is_refused(no_network):
    requests = pytest.importorskip("curl_cffi.requests")

    async def go():
        async with requests.AsyncSession() as session:
            await session.get("https://query1.finance.yahoo.com/")

    with pytest.raises(OSError, match="doesn't use the network"):
        asyncio.run(go())
    _refused(no_network, "https://query1.finance.yahoo.com/")


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Quiet(HTTPServer):
    def server_bind(self):  # HTTPServer's own bind reverse-resolves the address (a lookup that may leave the machine)
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


@contextlib.contextmanager
def _server(host):
    try:
        httpd = _Quiet((host, 0), _Hello)
    except OSError:
        pytest.skip(f"cannot listen on {host}")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "127.255.255.254"])
def test_all_of_127_0_0_0_8_stays_local_for_every_route(no_network, host):
    with _server(host) as port:
        with socket.create_connection((host, port), timeout=5):
            pass
        with socket.socket() as s:
            assert s.connect_ex((host, port)) == 0

        async def go():
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()

        asyncio.run(go())
    assert no_network == []


def test_curl_cffi_reaches_a_server_on_127_0_0_2(no_network):
    requests = pytest.importorskip("curl_cffi.requests")
    with _server("127.0.0.2") as port:
        assert requests.Session().get(f"http://127.0.0.2:{port}/", timeout=5).text == "hello"
    assert no_network == []


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.2", "::1", "::ffff:127.0.0.1"])
def test_a_local_name_or_address_is_looked_up_and_not_refused(no_network, host):
    assert socket.getaddrinfo(host, 80)
    assert no_network == []


@pytest.mark.parametrize("host", ["::1", "::ffff:127.0.0.1"])
def test_an_ipv6_loopback_connect_is_not_refused(no_network, host):
    with socket.socket(socket.AF_INET6) as s:
        s.settimeout(0.2)
        s.connect_ex((host, 9))  # nothing listens on port 9: the answer doesn't matter, only that it isn't refused
    assert no_network == []


def _unrefused(call, *args):
    """*call* (a lookup of a local address) answers or fails on its own terms, but is not refused by the guard."""
    try:
        call(*args)
    except OSError as exc:
        assert "doesn't use the network" not in str(exc)


@pytest.mark.parametrize("address", ["192.0.2.1", "2001:db8::1", "example.com", "8.8.8.8"])
def test_a_reverse_lookup_of_gethostbyaddr_off_this_machine_is_refused(no_network, address):
    # BUG-346: the guard refused forward lookups only; the resolver was still asked for an address's name.
    with pytest.raises(OSError, match="doesn't use the network"):
        socket.gethostbyaddr(address)
    assert [r[0] for r in no_network] == [address]
    no_network.clear()


@pytest.mark.parametrize("address", ["192.0.2.1", "2001:db8::1", "8.8.8.8"])
def test_getnameinfo_off_this_machine_is_refused(no_network, address):
    sockaddr = (address, 80, 0, 0) if ":" in address else (address, 80)
    with pytest.raises(OSError, match="doesn't use the network"):
        socket.getnameinfo(sockaddr, 0)
    assert [r[0] for r in no_network] == [address]
    no_network.clear()


def test_getfqdn_of_an_address_off_this_machine_is_refused_through_gethostbyaddr(no_network):
    assert socket.getfqdn("192.0.2.1") == "192.0.2.1"  # getfqdn swallows the lookup's OSError, as for a failed lookup
    assert [r[0] for r in no_network] == ["192.0.2.1"]
    no_network.clear()


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "::ffff:127.0.0.1", "localhost", "LOCALHOST"])
def test_reverse_lookups_of_local_addresses_are_not_refused(no_network, host):
    sockaddr = (host, 80, 0, 0) if ":" in host else (host, 80)
    flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
    _unrefused(socket.getnameinfo, sockaddr, flags)
    _unrefused(socket.gethostbyaddr, host)
    assert no_network == []
