"""Tests for the HTTP bridge (bridge.py): a real server on a free port, with the TV's methods replaced."""

import http.client
import json
import socket
import threading
import time

import pytest

import bridge
import tv_remote as core

HOST, MAC = "192.0.2.55", "00:00:5e:00:53:55"  # the TV the tests pretend to talk to (documentation addresses)
TOKEN = "test-token-0123456789"
APPS = [{"appId": "3201907018807", "name": "Netflix"}, {"appId": "111299001912", "name": "YouTube"}]


@pytest.fixture(autouse=True)
def config(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"host": HOST, "mac": MAC, "token": "test", "apps": APPS, "bridge_token": TOKEN}))
    monkeypatch.setattr(core, "CONFIG", path)
    return path


class RecordingTV(core.TV):
    """A TV whose calls are recorded instead of sent."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.calls = []
        self.state = "on"

    def key(self, key):
        self.calls.append(key)

    def launch(self, app):
        self.calls.append("launch:" + app["name"])

    def wake(self):
        self.calls.append("wake")

    def power_state(self):
        return self.state


@pytest.fixture
def tv():
    return RecordingTV(core.load_config())


@pytest.fixture
def server(tv):
    s = bridge.Bridge(tv, ("127.0.0.1", 0))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def request(server, method, path, token=TOKEN):
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        c.request(method, path, headers={"Authorization": f"Bearer {token}"} if token else {})
        r = c.getresponse()
        return r.status, json.loads(r.read())
    finally:
        c.close()


@pytest.mark.parametrize(("path", "sent"), [("/key/volup", "KEY_VOLUP"), ("/key/KEY_MUTE", "KEY_MUTE"),
                                            ("/key/power", "KEY_POWER")])
def test_keys(server, tv, path, sent):
    assert request(server, "POST", path) == (200, {"ok": True, "key": sent})
    assert tv.calls == [sent]


@pytest.mark.parametrize("path", ["/key/vol%20up", "/key/" + "x" * 41, "/key/a;b"])
def test_odd_key_names_are_refused(server, tv, path):
    status, _ = request(server, "POST", path)
    assert status == 400
    assert tv.calls == []


@pytest.mark.parametrize(("path", "app"), [("/launch/netflix", "Netflix"), ("/launch/YouTube", "YouTube"),
                                           ("/launch/you", "YouTube")])
def test_launch_by_key_or_name(server, tv, path, app):
    assert request(server, "POST", path) == (200, {"ok": True, "app": app})
    assert tv.calls == ["launch:" + app]


def test_launch_of_an_app_the_tv_lacks(server, tv):
    status, body = request(server, "POST", "/launch/hulu")  # in the catalog, not on this TV
    assert status == 404 and "hulu" in body["error"]
    assert tv.calls == []


@pytest.mark.parametrize("token", [None, "wrong", TOKEN + "x"])
def test_token_is_required(server, tv, token):
    status, body = request(server, "POST", "/key/volup", token=token)
    assert status == 401 and "token" in body["error"]
    assert tv.calls == []


def test_only_listed_clients(server, tv):
    tv.cfg["bridge_clients"] = ["192.0.2.30"]
    assert request(server, "POST", "/key/volup")[0] == 403
    tv.cfg["bridge_clients"] = ["127.0.0.1"]
    assert request(server, "POST", "/key/volup")[0] == 200


def test_status(server, tv):
    tv.state = "standby"
    assert request(server, "GET", "/status") == (200, {"power": "standby"})


def test_power_on_answers_at_once(server, tv):
    assert request(server, "POST", "/power/on") == (202, {"ok": True})
    deadline = time.time() + 3
    while not tv.calls and time.time() < deadline:
        time.sleep(0.01)
    assert tv.calls == ["wake"]


def test_power_off_runs_once_in_the_background(server, tv, monkeypatch):
    release = threading.Event()
    offs = []

    def turn_off():
        offs.append(1)
        release.wait(5)
        return True

    monkeypatch.setattr(tv, "turn_off", turn_off)
    assert request(server, "POST", "/power/off") == (202, {"ok": True})
    assert request(server, "POST", "/power/off") == (202, {"ok": True})  # pressed again while it runs
    release.set()
    deadline = time.time() + 3
    while server.turning_off.locked() and time.time() < deadline:
        time.sleep(0.02)
    assert offs == [1]
    assert request(server, "POST", "/power/off")[0] == 202  # free again afterwards
    deadline = time.time() + 3
    while len(offs) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert offs == [1, 1]


def test_unknown_requests(server):
    assert request(server, "GET", "/key/volup")[0] == 404
    assert request(server, "POST", "/nope")[0] == 404


def test_unreachable_tv_is_a_502(server, monkeypatch):
    def fail(key):
        raise OSError(113, "No route to host")
    monkeypatch.setattr(server.tv, "key", fail)
    status, body = request(server, "POST", "/key/volup")
    assert status == 502 and body["error"].startswith(f"Couldn't reach the TV at {HOST}")


def test_token_is_generated_and_saved(config):
    cfg = json.loads(config.read_text())
    del cfg["bridge_token"]
    config.write_text(json.dumps(cfg))
    token = bridge.ensure_token(core.load_config())
    assert len(token) >= 16
    assert json.loads(config.read_text())["bridge_token"] == token
    assert bridge.ensure_token(core.load_config()) == token  # kept from then on


def test_port_in_use_is_one_line(tv):
    taken = bridge.Bridge(tv, ("127.0.0.1", 0))
    try:
        tv.cfg["bridge_listen"] = "127.0.0.1"
        with pytest.raises(RuntimeError, match=r"^Couldn't listen on 127\.0\.0\.1:\d+"):
            bridge.serve(tv, [str(taken.server_address[1])])
    finally:
        taken.server_close()


def raw(server, head):
    with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as s:
        s.sendall(head.encode())
        return s.recv(4096).decode()


@pytest.mark.parametrize(("length", "status"), [("abc", "400"), ("-1", "400"), ("999999999", "413")])
def test_a_bad_body_length_is_refused_not_trusted(server, tv, length, status):
    reply = raw(server, f"POST /key/volup HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
                        f"Content-Length: {length}\r\n\r\n")
    assert reply.split()[1] == status and tv.calls == []


def test_a_small_body_is_read_and_ignored(server, tv):
    reply = raw(server, f"POST /key/volup HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
                        "Content-Length: 2\r\n\r\n{}")
    assert reply.split()[1] == "200" and tv.calls == ["KEY_VOLUP"]


def test_one_client_written_without_a_list_still_restricts(server, tv):
    tv.cfg["bridge_clients"] = "127.0.0.10"  # holds "127.0.0.1", but isn't it
    assert request(server, "POST", "/key/volup")[0] == 403
    tv.cfg["bridge_clients"] = "127.0.0.1"
    assert request(server, "POST", "/key/volup")[0] == 200
    tv.cfg["bridge_clients"] = {"127.0.0.1": True}
    assert request(server, "POST", "/key/volup")[0] == 403
