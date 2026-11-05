"""Shared by every test file: no test opens a TLS connection to a TV."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tv_remote as core

TEST_FINGERPRINT = "0" * 64  # the certificate the stand-in TV shows (TV._open_tls)


class FakeTLS:
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_tls(monkeypatch):
    """TV._open_tls() as a stand-in socket and TEST_FINGERPRINT; tests fake websocket.create_connection."""
    monkeypatch.setattr(core.TV, "_open_tls", lambda self: (FakeTLS(), TEST_FINGERPRINT))
