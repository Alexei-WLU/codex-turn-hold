#!/usr/bin/python3
"""Observe the shared local Codex server and own a GNOME suspend inhibitor."""

import argparse
import fcntl
import importlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import time
from dataclasses import dataclass


POLL_SECONDS = 2
CHECK_SECONDS = 5
IDLE_SECONDS = 180
RECOVERY_SECONDS = 180
APP_ID = "codex-turn-hold"
REASON = "Codex work or idle grace period"
WAIT_FLAGS = {"waitingOnApproval", "waitingOnUserInput"}


class MonitorError(Exception):
    """A fatal local setup or GNOME error."""


class StatusError(Exception):
    """A retryable failure to obtain a complete, trustworthy status snapshot."""


# Signals must unwind cleanup without being treated as retryable status errors.
class StopRequested(BaseException):
    def __init__(self, signum):
        self.signum = signum


def emit(message):
    print(f"codex-hold: {message}", flush=True)


def dependency(name, package):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise MonitorError(
            f"Missing {name} for /usr/bin/python3. Run: sudo apt-get install {package}"
        ) from exc


def server_socket():
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return home / "app-server-control" / "app-server-control.sock"


def lock_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "codex-turn-hold.lock"
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return state / "codex-turn-hold" / "monitor.lock"


class InstanceLock:
    """Keep the file in place: unlinking a flock file introduces an inode race."""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            info = os.fstat(self.fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1):
                raise MonitorError(f"Unsafe monitor lock: {self.path}")
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MonitorError("codex-hold is already running for this user.") from exc
            os.ftruncate(self.fd, 0)
            os.write(self.fd, f"{os.getpid()}\n".encode())
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def is_working(status):
    if not isinstance(status, dict) or not isinstance(status.get("type"), str):
        raise StatusError("Missing or malformed thread status")
    kind = status["type"]
    if kind == "active":
        flags = status.get("activeFlags")
        if not isinstance(flags, list) or any(
            not isinstance(flag, str) or flag not in WAIT_FLAGS for flag in flags
        ):
            raise StatusError("Missing, malformed, or unknown activeFlags")
        # Waiting for approval or input is nonworking, despite the active type.
        return not flags
    if kind in ("idle", "notLoaded") and "activeFlags" not in status:
        return False
    if kind == "systemError":
        raise StatusError("Codex reports systemError")
    raise StatusError("Unknown or inconsistent thread status")


@dataclass(frozen=True)
class Snapshot:
    loaded: int
    working: int


def read_snapshot(rpc):
    """Enumerate every page and validate every thread, even after finding work."""
    ids = set()
    cursors = set()
    cursor = None
    while True:
        page = rpc("thread/loaded/list", {"limit": 100, "cursor": cursor})
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise StatusError("Malformed loaded-thread page")
        for thread_id in page["data"]:
            if not isinstance(thread_id, str) or not thread_id:
                raise StatusError("Malformed loaded-thread id")
            ids.add(thread_id)
        cursor = page.get("nextCursor")  # Optional in the 0.164.0 schema.
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise StatusError("Malformed or repeated pagination cursor")
        cursors.add(cursor)

    working = 0
    for thread_id in sorted(ids):
        result = rpc("thread/read", {"threadId": thread_id, "includeTurns": False})
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise StatusError("Missing or mismatched thread metadata")
        working += is_working(thread.get("status"))
    return Snapshot(len(ids), working)


class DeadlineSocket:
    """Apply the scan deadline to every fragment, including WebSocket control traffic."""

    def __init__(self, raw, remaining):
        self.raw = raw
        self.remaining = remaining

    def gettimeout(self):
        return self.remaining()

    def recv(self, size):
        self.raw.settimeout(self.remaining())
        data = self.raw.recv(size)
        self.remaining()
        return data

    def send(self, data):
        self.raw.settimeout(self.remaining())
        sent = self.raw.send(data)
        self.remaining()
        return sent

    def __getattr__(self, name):
        return getattr(self.raw, name)


# Reuse one local connection; a failed scan closes it so the next poll reconnects.
class CodexClient:
    def __init__(self, path, websocket_module, clock=time.monotonic):
        self.path = path
        self.websocket = websocket_module
        self.clock = clock
        self.ws = None
        self.request_id = 0
        self.deadline = None

    def remaining(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise StatusError("Codex status check timed out")
        return remaining

    def connect(self, deadline):
        self.deadline = deadline
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            raw.settimeout(self.remaining(deadline))
            raw.connect(str(self.path))
            self.ws = self.websocket.WebSocket(enable_multithread=False)
            # Prevent redirects from replacing the supplied Unix socket with TCP.
            try:
                self.ws.connect(
                    "ws://localhost/",
                    socket=DeadlineSocket(raw, lambda: self.remaining(self.deadline)),
                    timeout=self.remaining(deadline), suppress_origin=True, redirect_limit=0,
                )
            except (ValueError, IndexError, OverflowError) as exc:
                raise StatusError("Malformed Codex WebSocket handshake") from exc
            if self.ws.getstatus() != 101:
                raise StatusError("Codex did not accept the local WebSocket upgrade")
            self.rpc("initialize", {
                "clientInfo": {"name": "codex_turn_hold", "version": "1.0.0"},
            }, deadline)
            self.ws.settimeout(self.remaining(deadline))
            self.ws.send(json.dumps({"method": "initialized", "params": {}}))
        except BaseException:
            raw.close()
            self.close()
            raise

    def rpc(self, method, params, deadline):
        self.request_id += 1
        request_id = self.request_id
        self.ws.settimeout(self.remaining(deadline))
        self.ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            self.ws.settimeout(self.remaining(deadline))
            raw = self.ws.recv()
            if not raw:
                raise StatusError("Codex connection closed")
            try:
                message = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise StatusError("Malformed Codex JSON response") from exc
            if not isinstance(message, dict):
                raise StatusError("Malformed Codex RPC response")
            if "id" not in message and isinstance(message.get("method"), str):
                continue  # Notifications do not replace a full status check.
            if ("method" in message or type(message.get("id")) is not int
                    or message["id"] != request_id):
                raise StatusError("Unexpected Codex RPC response or server request")
            if "error" in message:
                raise StatusError(f"{method} returned an RPC error")
            if not isinstance(message.get("result"), dict):
                raise StatusError(f"{method} returned a malformed result")
            return message["result"]

    def check(self, deadline=None):
        # Connection setup, pagination, and all thread reads share one deadline.
        if deadline is None:
            deadline = self.clock() + CHECK_SECONDS
        self.deadline = deadline
        try:
            if self.ws is None:
                self.connect(deadline)
            snapshot = read_snapshot(lambda method, params: self.rpc(method, params, deadline))
            self.remaining(deadline)  # A late final response cannot restart idle grace.
            return snapshot
        except StatusError:
            self.close()
            raise
        except (OSError, UnicodeError, self.websocket.WebSocketException) as exc:
            self.close()
            raise StatusError(f"Cannot read Codex at {self.path}: {exc}") from exc

    def close(self):
        if self.ws is not None:
            self.ws.shutdown()  # No blocking WebSocket close handshake.
            self.ws = None


class GnomeInhibitor:
    def __init__(self, dbus_module):
        self.dbus = dbus_module
        self.bus = None
        self.cookie = None
        try:
            # GNOME drops our inhibitors when this private connection closes.
            self.bus = self.dbus.SessionBus(private=True)
            self.bus.set_exit_on_disconnect(False)
            self.owner = self.name_owner()
            proxy = self.bus.get_object(self.owner, "/org/gnome/SessionManager", introspect=False)
            self.manager = self.dbus.Interface(proxy, "org.gnome.SessionManager")
        except BaseException as exc:
            if self.bus is not None:
                self.bus.close()
            if isinstance(exc, self.dbus.DBusException):
                raise MonitorError(
                    f"Cannot connect to GNOME SessionManager; run in your desktop terminal: {exc}"
                ) from exc
            raise

    @property
    def held(self):
        return self.cookie is not None

    def name_owner(self):
        return self.bus.call_blocking(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "GetNameOwner", "s", ("org.gnome.SessionManager",), timeout=2,
        )

    def check_connection(self):
        try:
            owner = self.name_owner()
            # A replacement session manager cannot honor the old owner's cookie.
            if owner != self.owner:
                raise MonitorError("GNOME SessionManager restarted; restart codex-hold.")
        except self.dbus.DBusException as exc:
            raise MonitorError(f"GNOME connection lost; restart codex-hold: {exc}") from exc

    def acquire(self):
        if not self.held:
            try:
                # GNOME flag 4 inhibits suspend only, leaving screen blanking unchanged.
                self.cookie = self.manager.Inhibit(
                    APP_ID, self.dbus.UInt32(0), REASON, self.dbus.UInt32(4), timeout=2,
                )
            except self.dbus.DBusException as exc:
                raise MonitorError(f"Cannot acquire GNOME suspend inhibitor: {exc}") from exc

    def release(self):
        if self.held:
            try:
                self.manager.Uninhibit(self.cookie, timeout=2)
                self.cookie = None
            except self.dbus.DBusException as exc:
                raise MonitorError(f"Cannot release GNOME inhibitor; closing connection: {exc}") from exc

    def close(self):
        try:
            self.release()
        finally:
            self.bus.close()  # Also clears the inhibitor if Uninhibit failed.
            self.cookie = None


# Keep timer decisions separate from I/O so tests can advance time explicitly.
class HoldPolicy:
    def __init__(self, inhibitor, clock=time.monotonic, log=emit):
        self.inhibitor = inhibitor
        self.clock = clock
        self.log = log
        self.idle_deadline = None
        self.recovery_deadline = None
        self.state = "starting"

    def success(self, snapshot):
        # Status received at or after expiry cannot renew the old recovery hold.
        self.expire_recovery()
        now = self.clock()
        if self.recovery_deadline is not None:
            self.log("Status recovered.")
            self.recovery_deadline = None
        if snapshot.working:
            # Cancel the countdown without releasing an already-held inhibitor.
            self.idle_deadline = None
            if not self.inhibitor.held:
                self.inhibitor.acquire()
                self.log("Active work; suspend inhibited.")
            elif self.state != "active":
                self.log("Active work resumed; keeping suspend inhibited.")
            self.state = "active"
        elif self.inhibitor.held:
            # Idle startup never acquires a hold; only an existing hold gets grace.
            if self.idle_deadline is None:
                self.idle_deadline = now + IDLE_SECONDS
                self.log("All threads idle or waiting; allowing sleep in 180s if this continues.")
                self.state = "idle-delay"
            elif now >= self.idle_deadline:
                self.inhibitor.release()
                self.idle_deadline = None
                self.log("Sleep allowed; idle for 180s. Monitoring continues.")
                self.state = "sleep"
        elif self.state != "sleep":
            self.log("Sleep allowed; no active work. Monitoring continues.")
            self.state = "sleep"

    def failure(self, error):
        # Unknown status breaks continuous idle time; recovery gets its own timer.
        self.idle_deadline = None
        if self.recovery_deadline is None:
            # Repeated failures must not push this deadline forward indefinitely.
            self.recovery_deadline = self.clock() + RECOVERY_SECONDS
            action = "keeping the existing hold for up to 180s" if self.inhibitor.held else "sleep allowed"
            self.log(f"Status unavailable ({error}); {action}; retrying.")
            self.state = "recovering"
        self.expire_recovery()

    def expire_recovery(self):
        if (self.recovery_deadline is not None and self.inhibitor.held
                and self.clock() >= self.recovery_deadline):
            self.inhibitor.release()
            self.log("Sleep allowed; status recovery exceeded 180s. Retrying.")
            self.state = "recovery-expired"


def monitor(client, inhibitor, clock=time.monotonic, sleep=time.sleep, log=emit):
    policy = HoldPolicy(inhibitor, clock, log)
    log(f"Monitoring {client.path} every 2s; Ctrl+C to stop.")
    while True:
        started = clock()
        policy.expire_recovery()
        inhibitor.check_connection()
        policy.expire_recovery()
        deadline = clock() + CHECK_SECONDS
        # A slow status check must not carry the hold past recovery's deadline.
        if policy.recovery_deadline is not None and inhibitor.held:
            deadline = min(deadline, policy.recovery_deadline)
        try:
            snapshot = client.check(deadline)
        except StatusError as exc:
            policy.failure(exc)
        else:
            policy.success(snapshot)
        # Count scan time toward the poll interval and wake for recovery expiry.
        delay = max(0, started + POLL_SECONDS - clock())
        if policy.recovery_deadline is not None and inhibitor.held:
            delay = min(delay, max(0, policy.recovery_deadline - clock()))
        sleep(delay)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read Codex status once; do not inhibit sleep")
    args = parser.parse_args(argv)
    client = None
    inhibitor = None
    exit_code = 0

    def stop(signum, _frame):
        raise StopRequested(signum)

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    try:
        client = CodexClient(server_socket(), dependency("websocket", "python3-websocket"))
        if args.check:
            # Diagnostics can run alongside a monitor without a lock or inhibitor.
            snapshot = client.check()
            print(json.dumps({"loadedThreads": snapshot.loaded, "workingThreads": snapshot.working}))
        else:
            with InstanceLock(lock_path()):
                inhibitor = GnomeInhibitor(dependency("dbus", "python3-dbus"))
                try:
                    monitor(client, inhibitor)
                finally:
                    # Finish cleanup even if a second terminal signal arrives.
                    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                        signal.signal(signum, signal.SIG_IGN)
                    inhibitor.close()
                    inhibitor = None
    except StopRequested as exc:
        emit("Stopped; this monitor's suspend hold is released.")
        exit_code = 128 + exc.signum
    except (MonitorError, StatusError, OSError) as exc:
        print(f"codex-hold: error: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        if client is not None:
            client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
