"""The suite's network guard (see ``tests/conftest.py``)."""

import socket

import pytest


def test_a_connection_off_this_machine_is_refused(no_network):
    with pytest.raises(OSError, match="doesn't use the network"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)
    assert no_network == [("192.0.2.1", 443)]
    no_network.clear()  # refused as intended, so the teardown doesn't fail the test
