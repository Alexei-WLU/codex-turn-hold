"""Run with /usr/bin/python3 -B -m unittest -v; no desktop or third-party imports."""

import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


def load(name, filename):
    # Load the hyphenated executable filenames without running their main blocks.
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


monitor = load("codex_monitor", "codex-monitor.py")
installer = load("install_monitor", "install-monitor.py")
ACTIVE = {"type": "active", "activeFlags": []}
IDLE = {"type": "idle"}


class Clock:
    now = 0

    def __call__(self):
        return self.now


# Reject duplicate transitions to expose dropped or unnecessarily reacquired holds.
class Inhibitor:
    def __init__(self):
        self.held = False
        self.events = []

    def acquire(self):
        if self.held:
            raise AssertionError("Double acquisition")
        self.held = True
        self.events.append("acquire")

    def release(self):
        if not self.held:
            raise AssertionError("Double release")
        self.held = False
        self.events.append("release")

    def close(self):
        if self.held:
            self.release()

    def check_connection(self):
        pass


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.inhibitor = Inhibitor()
        self.messages = []
        self.policy = monitor.HoldPolicy(self.inhibitor, self.clock, self.messages.append)

    def ok(self, at, working=0):
        self.clock.now = at
        self.policy.success(monitor.Snapshot(3, working))

    def fail(self, at):
        self.clock.now = at
        self.policy.failure(monitor.StatusError("test failure"))

    def test_start_idle_never_acquires(self):
        self.ok(0)
        self.ok(200)
        self.assertEqual([], self.inhibitor.events)
        self.assertEqual(1, len(self.messages))

    def test_start_during_work_and_two_complete_turns(self):
        self.ok(0, 1)
        self.ok(2)
        self.ok(181.999)
        self.assertTrue(self.inhibitor.held)
        self.ok(182)
        self.assertFalse(self.inhibitor.held)
        self.ok(200, 1)
        self.ok(202)
        self.ok(383)
        self.assertEqual(["acquire", "release", "acquire", "release"], self.inhibitor.events)

    def test_work_resuming_during_countdown_never_drops_hold(self):
        self.ok(0, 1)
        self.ok(2)
        self.ok(181, 1)
        self.ok(184)
        self.ok(363.999)
        self.assertEqual(["acquire"], self.inhibitor.events)
        self.ok(364)
        self.assertEqual(["acquire", "release"], self.inhibitor.events)

    def test_overlapping_work_keeps_one_inhibitor(self):
        for at, count in ((0, 1), (2, 2), (4, 1), (200, 3)):
            self.ok(at, count)
        self.assertEqual(["acquire"], self.inhibitor.events)
        self.assertIsNone(self.policy.idle_deadline)

    def test_cancellation_and_waiting_use_idle_delay(self):
        for status in (IDLE, {"type": "notLoaded"},
                       {"type": "active", "activeFlags": ["waitingOnApproval"]},
                       {"type": "active", "activeFlags": ["waitingOnUserInput"]}):
            with self.subTest(status=status):
                self.setUp()
                self.ok(0, 1)
                self.ok(10, int(monitor.is_working(status)))
                self.ok(189.999)
                self.assertTrue(self.inhibitor.held)
                self.ok(190)
                self.assertFalse(self.inhibitor.held)

    def test_failures_cancel_idle_and_do_not_extend_recovery(self):
        self.ok(0, 1)
        self.ok(10)
        self.fail(100)
        self.assertIsNone(self.policy.idle_deadline)
        for at in (101, 179, 190, 279.999):
            self.fail(at)
            self.assertEqual(280, self.policy.recovery_deadline)
            self.assertTrue(self.inhibitor.held)
        self.fail(280)
        self.fail(500)
        self.assertEqual(["acquire", "release"], self.inhibitor.events)
        self.ok(502, 1)
        self.assertEqual(["acquire", "release", "acquire"], self.inhibitor.events)

    def test_recovered_idle_starts_fresh_180_seconds(self):
        self.ok(0, 1)
        self.ok(10)
        self.fail(100)
        self.ok(279)
        self.assertIsNone(self.policy.recovery_deadline)
        self.assertEqual(459, self.policy.idle_deadline)
        self.ok(458.999)
        self.assertTrue(self.inhibitor.held)
        self.ok(459)
        self.assertFalse(self.inhibitor.held)

    def test_active_recovery_keeps_hold_and_new_failure_gets_new_deadline(self):
        self.ok(0, 1)
        self.fail(10)
        self.ok(189, 1)
        self.fail(200)
        self.assertEqual(380, self.policy.recovery_deadline)
        self.assertEqual(["acquire"], self.inhibitor.events)

    def test_failure_at_start_never_acquires_and_idle_recovery_stays_unheld(self):
        for at in (0, 100, 180, 500):
            self.fail(at)
        self.ok(502)
        self.assertEqual([], self.inhibitor.events)
        self.ok(504, 1)
        self.assertTrue(self.inhibitor.held)

    def test_no_transition_log_spam(self):
        self.ok(0, 1)
        for at in range(2, 100, 2):
            self.ok(at, 1)
        self.assertEqual(1, len(self.messages))
        for at in range(100, 200, 2):
            self.fail(at)
        self.assertEqual(2, len(self.messages))


class SnapshotTests(unittest.TestCase):
    def test_pages_include_active_subagent_and_waiting_parent(self):
        calls = []
        statuses = {
            "parent": {"type": "active", "activeFlags": ["waitingOnUserInput"]},
            "other": IDLE,
            "child": ACTIVE,
        }

        def rpc(method, params):
            calls.append((method, params))
            if method == "thread/loaded/list":
                if params["cursor"] is None:
                    return {"data": ["parent", "other"], "nextCursor": "page2"}
                self.assertEqual("page2", params["cursor"])
                return {"data": ["child"], "nextCursor": None}
            self.assertEqual("thread/read", method)
            self.assertIs(False, params["includeTurns"])
            return {"thread": {"id": params["threadId"], "status": statuses[params["threadId"]]}}

        self.assertEqual(monitor.Snapshot(3, 1), monitor.read_snapshot(rpc))
        self.assertEqual(5, len(calls))

    def test_empty_list_and_optional_cursor(self):
        self.assertEqual(monitor.Snapshot(0, 0), monitor.read_snapshot(lambda *_: {"data": []}))

    def test_malformed_page_or_cursor_fails(self):
        for page in (None, {}, {"data": "thread"}, {"data": [None]}, {"data": [""]},
                     {"data": [], "nextCursor": []}, {"data": [], "nextCursor": ""},
                     {"data": [], "nextCursor": "loop"}):
            with self.subTest(page=page), self.assertRaises(monitor.StatusError):
                monitor.read_snapshot(lambda *_: page)

    def test_every_status_validated_even_if_work_found(self):
        def rpc(method, params):
            if method == "thread/loaded/list":
                return {"data": ["a-active", "b-broken"]}
            tid = params["threadId"]
            return {"thread": {"id": tid, "status": ACTIVE if tid == "a-active" else None}}
        with self.assertRaises(monitor.StatusError):
            monitor.read_snapshot(rpc)

    def test_mismatched_or_missing_thread_fails(self):
        for result in ({}, None, {"thread": []}, {"thread": {"id": "wrong", "status": IDLE}}):
            with self.subTest(result=result), self.assertRaises(monitor.StatusError):
                monitor.read_snapshot(lambda method, _: {"data": ["expected"]}
                                      if method == "thread/loaded/list" else result)

    def test_unknown_and_malformed_statuses_fail(self):
        for status in (None, "idle", {}, {"type": []}, {"type": "future"},
                       {"type": "systemError"}, {"type": "active"},
                       {"type": "active", "activeFlags": None},
                       {"type": "active", "activeFlags": "waitingOnApproval"},
                       {"type": "active", "activeFlags": ["future"]},
                       {"type": "active", "activeFlags": [[]]},
                       {"type": "idle", "activeFlags": []}):
            with self.subTest(status=status), self.assertRaises(monitor.StatusError):
                monitor.is_working(status)

    def test_both_wait_flags_are_nonworking(self):
        self.assertFalse(monitor.is_working({"type": "active", "activeFlags": sorted(monitor.WAIT_FLAGS)}))


class FakeWebSocket:
    def __init__(self, malformed=False, status=ACTIVE):
        self.malformed = malformed
        self.status = status
        self.messages = []
        self.sent = []
        self.closed = False

    def connect(self, *args, **kwargs):
        self.connect_args = (args, kwargs)

    def settimeout(self, seconds):
        if seconds <= 0:
            raise AssertionError("Unbounded timeout")

    def send(self, raw):
        request = json.loads(raw)
        self.sent.append(request)
        if "id" not in request:
            return
        if request["method"] == "initialize":
            result = {"userAgent": "fixture"}
        elif request["method"] == "thread/loaded/list":
            if self.malformed:
                self.messages.append("broken JSON")
                return
            result = {"data": ["thread"]}
        else:
            result = {"thread": {"id": "thread", "status": self.status}}
        # Interleave notifications to verify that the client waits for its response.
        self.messages.append(json.dumps({"method": "thread/status/changed", "params": {}}))
        self.messages.append(json.dumps({"id": request["id"], "result": result}))

    def recv(self):
        return self.messages.pop(0)

    def shutdown(self):
        self.closed = True


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.module = Mock(WebSocketException=type("WebSocketException", (Exception,), {}))
        self.client = monitor.CodexClient(Path("/fixture/socket"), self.module, self.clock)

    def test_reconnect_and_initialize_after_bad_response(self):
        broken = FakeWebSocket(malformed=True)
        good = FakeWebSocket()
        self.module.WebSocket.side_effect = [broken, good]
        with patch.object(monitor.socket, "socket") as raw:
            with self.assertRaises(monitor.StatusError):
                self.client.check()
            self.assertTrue(broken.closed)
            self.assertIsNone(self.client.ws)
            self.assertEqual(monitor.Snapshot(1, 1), self.client.check())
            self.assertEqual(monitor.Snapshot(1, 1), self.client.check())
            self.assertEqual(2, raw.call_count)
        self.assertEqual("initialize", good.sent[0]["method"])
        self.assertEqual("initialized", good.sent[1]["method"])
        self.assertTrue(good.connect_args[1]["suppress_origin"])
        self.client.close()
        self.assertTrue(good.closed)

    def test_closed_socket_rpc_errors_and_malformed_responses_fail(self):
        responses = ("", "null", "[]", "{", '{"id":1,"error":{}}',
                     '{"id":true,"result":{}}', '{"id":2,"result":{}}',
                     '{"id":1,"result":null}', '{"id":1,"method":"approval"}')
        for raw in responses:
            with self.subTest(raw=raw):
                self.client.request_id = 0
                self.client.ws = Mock()
                self.client.ws.recv.return_value = raw
                with self.assertRaises(monitor.StatusError):
                    self.client.check()
                self.assertIsNone(self.client.ws)

    def test_global_deadline_stops_notification_stream(self):
        self.client.ws = Mock()

        def recv():
            self.clock.now += 1
            return '{"method":"notification"}'

        self.client.ws.recv.side_effect = recv
        with self.assertRaisesRegex(monitor.StatusError, "timed out"):
            self.client.check(deadline=5)
        self.assertEqual(5, self.clock.now)

    def test_unix_socket_failure_is_retryable(self):
        with patch.object(monitor.socket, "socket") as raw:
            raw.return_value.connect.side_effect = FileNotFoundError("absent")
            with self.assertRaisesRegex(monitor.StatusError, "Cannot read Codex"):
                self.client.check()
            raw.return_value.close.assert_called_once()

    def test_codex_home_is_honored(self):
        with patch.dict(os.environ, {"CODEX_HOME": "/custom home"}):
            self.assertEqual(Path("/custom home/app-server-control/app-server-control.sock"), monitor.server_socket())


class RuntimeTests(unittest.TestCase):
    def test_duplicate_lock_and_reuse_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.lock"
            with monitor.InstanceLock(path):
                with self.assertRaisesRegex(monitor.MonitorError, "already running"):
                    with monitor.InstanceLock(path):
                        pass
            with monitor.InstanceLock(path):
                pass
            self.assertTrue(path.exists())

    def test_loop_bounds_inflight_check_by_recovery_deadline(self):
        clock = Clock()
        inhibitor = Inhibitor()
        samples = []

        class Client:
            path = "/fixture"

            def check(self, deadline):
                samples.append((clock.now, deadline, inhibitor.held))
                if clock.now == 0:
                    return monitor.Snapshot(1, 1)
                if clock.now >= 182:
                    raise monitor.StopRequested(signal.SIGTERM)
                if clock.now != 2:  # First failure is immediate; later ones time out.
                    clock.now = deadline
                raise monitor.StatusError("timeout")

        def sleep(delay):
            clock.now += delay

        with self.assertRaises(monitor.StopRequested):
            monitor.monitor(Client(), inhibitor, clock, sleep, lambda _: None)
        self.assertEqual(["acquire", "release"], inhibitor.events)
        # The last failing scan gets three seconds, reaching the recovery limit.
        self.assertIn((179, 182, True), samples)
        self.assertFalse(samples[-1][2])
        self.assertEqual(182, samples[-1][0])

    def test_sigint_sigterm_and_hangup_cleanup_and_unlock(self):
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as directory:
                inhibitor = Inhibitor()
                client = Mock()

                def interrupted(*_):
                    inhibitor.acquire()
                    raise monitor.StopRequested(signum)

                path = Path(directory) / "lock"
                with patch.object(monitor, "dependency"), patch.object(monitor, "CodexClient", return_value=client), \
                        patch.object(monitor, "GnomeInhibitor", return_value=inhibitor), \
                        patch.object(monitor, "lock_path", return_value=path), \
                        patch.object(monitor, "monitor", side_effect=interrupted), \
                        patch.object(monitor.signal, "signal"), patch.object(monitor, "emit"):
                    self.assertEqual(128 + signum, monitor.main([]))
                self.assertFalse(inhibitor.held)
                client.close.assert_called_once()
                with monitor.InstanceLock(path):
                    pass

    def test_missing_dependency_has_actionable_error(self):
        with patch.object(monitor.importlib, "import_module", side_effect=ImportError):
            with self.assertRaisesRegex(monitor.MonitorError, "sudo apt-get install python3-websocket"):
                monitor.dependency("websocket", "python3-websocket")

    def test_gnome_flags_cleanup_and_lost_connection(self):
        dbus = Mock(DBusException=type("DBusException", (Exception,), {}), UInt32=int)
        bus = dbus.SessionBus.return_value
        bus.call_blocking.return_value = ":1.42"
        interface = dbus.Interface.return_value
        interface.Inhibit.return_value = 123
        inhibitor = monitor.GnomeInhibitor(dbus)
        inhibitor.acquire()
        inhibitor.acquire()
        interface.Inhibit.assert_called_once_with(monitor.APP_ID, 0, monitor.REASON, 4, timeout=2)
        bus.call_blocking.return_value = ":1.43"
        with self.assertRaisesRegex(monitor.MonitorError, "restarted"):
            inhibitor.check_connection()
        interface.Uninhibit.side_effect = dbus.DBusException("lost connection")
        with self.assertRaises(monitor.MonitorError):
            inhibitor.close()
        bus.close.assert_called_once()
        self.assertFalse(inhibitor.held)


class MigrationTests(unittest.TestCase):
    def test_copied_bash_function_migration_preserves_surroundings(self):
        old = "# before\n" + installer.BEGIN + "# legacy comments\n" + installer.LEGACY_FUNCTION + installer.END + "# after\n"
        expected = "# before\n" + installer.BEGIN + installer.SOURCE + installer.END + "# after\n"
        self.assertEqual(expected, installer.migrate_bashrc(old))
        self.assertEqual(expected, installer.migrate_bashrc(expected))

    def test_unmarked_function_is_replaced(self):
        migrated = installer.migrate_bashrc("# before\n" + installer.LEGACY_FUNCTION)
        self.assertNotIn("rm -f", migrated)
        self.assertIn(installer.SOURCE, migrated)

    def test_custom_function_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            installer.migrate_bashrc("codex-hold() { echo custom; }\n")

    def test_exact_notify_removed_single_and_multiline(self):
        hook = Path("/fixture/.codex/turn-done.sh")
        for value in ('["bash", "/fixture/.codex/turn-done.sh"]',
                      '[\n  "bash",\n  "/fixture/.codex/turn-done.sh",\n] # comment'):
            text = f'# before\nnotify = {value}\nmodel = "example"\n[tui]\nkey = true\n'
            migrated, removed = installer.migrate_config(text, hook)
            self.assertTrue(removed)
            self.assertEqual('# before\nmodel = "example"\n[tui]\nkey = true\n', migrated)

    def test_custom_or_nested_notify_is_preserved(self):
        for text in ('notify = ["bash", "/other/turn-done.sh"]\n',
                     'notify = ["bash", "/fixture/.codex/turn-done.sh", "extra"]\n',
                     '[tui]\nnotify = ["bash", "/fixture/.codex/turn-done.sh"]\n', ''):
            self.assertEqual((text, False), installer.migrate_config(text, Path("/fixture/.codex/turn-done.sh")))


if __name__ == "__main__":
    unittest.main()
