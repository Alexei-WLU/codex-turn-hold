"""Run with /usr/bin/python3 -B -m unittest -v; no desktop or third-party imports."""

import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout


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

    def test_idle_at_or_after_recovery_expiry_cannot_extend_hold(self):
        for at in (182, 183):
            with self.subTest(at=at):
                self.setUp()
                self.ok(0, 1)
                self.fail(2)
                self.ok(at)
                self.assertFalse(self.inhibitor.held)
                self.assertIsNone(self.policy.idle_deadline)
                self.assertEqual(["acquire", "release"], self.inhibitor.events)

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

    def getstatus(self):
        return 101

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


class DeadlineSocketTests(unittest.TestCase):
    def test_fragments_and_partial_writes_share_one_deadline(self):
        for operation, argument in (("recv", 1), ("send", b"fragment")):
            with self.subTest(operation=operation):
                clock = Clock()
                client = monitor.CodexClient(Path("/fixture"), Mock(), clock)
                raw = Mock()

                def transfer(_):
                    clock.now += 1
                    return b"x" if operation == "recv" else 1

                getattr(raw, operation).side_effect = transfer
                transport = monitor.DeadlineSocket(raw, lambda: client.remaining(3))
                for _ in range(2):
                    getattr(transport, operation)(argument)
                with self.assertRaisesRegex(monitor.StatusError, "timed out"):
                    getattr(transport, operation)(argument)
                self.assertEqual(3, clock.now)
                self.assertEqual([3, 2, 1], [call.args[0] for call in raw.settimeout.call_args_list])
                with self.assertRaises(monitor.StatusError):
                    transport.gettimeout()
                transport.close()
                raw.close.assert_called_once()


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
            self.clock.now = 100  # The reused connection needs a fresh scan deadline.
            self.assertEqual(monitor.Snapshot(1, 1), self.client.check())
            self.assertEqual(5, good.connect_args[1]["socket"].gettimeout())
            self.assertEqual(2, raw.call_count)
        self.assertEqual("initialize", good.sent[0]["method"])
        self.assertEqual("initialized", good.sent[1]["method"])
        self.assertTrue(good.connect_args[1]["suppress_origin"])
        self.client.close()
        self.assertTrue(good.closed)

    def test_closed_socket_rpc_errors_and_malformed_responses_fail(self):
        responses = ("", "null", "[]", "{", '{"id":1,"error":{}}',
                     '{"id":true,"result":{}}', '{"id":2,"result":{}}',
                     '{"id":1,"result":null}', '{"id":1,"method":"approval"}',
                     '{"id":1,"method":"approval","result":{"data":[]}}')
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

    def test_response_at_or_after_deadline_is_rejected(self):
        for at in (5, 6):
            with self.subTest(at=at):
                self.setUp()
                self.client.ws = Mock()

                def recv():
                    self.clock.now = at
                    return json.dumps({"id": 1, "result": {"data": []}})

                self.client.ws.recv.side_effect = recv
                with self.assertRaisesRegex(monitor.StatusError, "timed out"):
                    self.client.check(deadline=5)
                self.assertIsNone(self.client.ws)

    def test_deeply_nested_json_is_retryable(self):
        self.client.ws = Mock()
        self.client.ws.recv.return_value = "[" * 10000 + "0" + "]" * 10000
        with self.assertRaises(monitor.StatusError):
            self.client.check()
        self.assertIsNone(self.client.ws)

    def test_malformed_handshake_is_retryable(self):
        for error in (ValueError("bad HTTP status"), IndexError("missing HTTP status"),
                      OverflowError("HTTP content length is too large"),
                      UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid header")):
            with self.subTest(error=error), patch.object(monitor.socket, "socket") as raw:
                self.module.WebSocket.return_value.connect.side_effect = error
                with self.assertRaises(monitor.StatusError):
                    self.client.check()
                self.assertIsNone(self.client.ws)
                raw.return_value.close.assert_called()

    def test_redirect_is_refused_without_following_it(self):
        ws = self.module.WebSocket.return_value
        ws.getstatus.return_value = 302
        with patch.object(monitor.socket, "socket"):
            with self.assertRaises(monitor.StatusError):
                self.client.check()
        self.assertEqual(0, ws.connect.call_args.kwargs.get("redirect_limit"))
        ws.send.assert_not_called()

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
    def test_hardlinked_lock_does_not_truncate_another_file(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original"
            original.write_bytes(b"preserve this file")
            path = Path(directory) / "lock"
            os.link(original, path)
            with self.assertRaisesRegex(monitor.MonitorError, "Unsafe"):
                with monitor.InstanceLock(path):
                    pass
            self.assertEqual(b"preserve this file", original.read_bytes())

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

    def test_failed_or_interrupted_gnome_constructor_closes_private_bus(self):
        dbus = Mock(DBusException=type("DBusException", (Exception,), {}))
        bus = dbus.SessionBus.return_value
        for error, expected in ((monitor.StopRequested(signal.SIGTERM), monitor.StopRequested),
                                (dbus.DBusException("no session manager"), monitor.MonitorError)):
            with self.subTest(error=error):
                bus.reset_mock()
                bus.call_blocking.side_effect = error
                with self.assertRaises(expected):
                    monitor.GnomeInhibitor(dbus)
                bus.close.assert_called_once()


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
        for text in ("codex-hold() { echo custom; }\n",
                     "codex-hold( ) { echo custom; }\n",
                     "function codex-hold { echo custom; }\n",
                     "if true; then codex-hold() { echo custom; }; fi\n",
                     "codex-\\\nhold() { echo custom; }\n",
                     "function 'codex-hold' { echo custom; }\n",
                     "alias codex-hold='echo custom'\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                installer.migrate_bashrc(text)

    def test_legacy_text_in_heredoc_or_nested_block_is_not_rewritten(self):
        for prefix, suffix in (("cat <<'EXAMPLE'\n", "EXAMPLE\n"),
                               ("if true; then\n", "fi\n"),
                               ("message=\"\n", "\"\n")):
            for body in (installer.LEGACY_FUNCTION,
                         installer.BEGIN + installer.SOURCE + installer.END):
                text = prefix + body + suffix
                with self.subTest(prefix=prefix, body=body), self.assertRaises(ValueError):
                    installer.migrate_bashrc(text)

    def test_markers_must_be_separate_ordered_lines(self):
        for text in ("# example " + installer.BEGIN + installer.SOURCE + installer.END,
                     installer.END + installer.BEGIN + installer.SOURCE,
                     installer.BEGIN + installer.SOURCE + "# example " + installer.END):
            with self.subTest(text=text), self.assertRaises(ValueError):
                installer.migrate_bashrc(text)

    def test_incomplete_shell_suffix_is_not_used_as_insertion_point(self):
        for text in ("cat <<'EXAMPLE'\nunfinished document\n", "echo continued \\\n", "echo continued \\"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                installer.migrate_bashrc(text)

    def test_similarly_named_function_is_not_modified(self):
        original = "my-" + installer.LEGACY_FUNCTION
        migrated = installer.migrate_bashrc(original)
        self.assertTrue(migrated.startswith(original))
        self.assertIn(installer.SOURCE, migrated)

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

    def test_unrelated_nan_and_multiline_values_survive_notify_removal(self):
        hook = Path("/fixture/.codex/turn-done.sh")
        remaining = 'value = nan\narray = [1.0, -nan, +inf]\ntext = """\nnotify = ["example"]\n"""\n'
        original = f'notify = ["bash", "{hook}"]\n' + remaining
        self.assertEqual((remaining, True), installer.migrate_config(original, hook))


class LauncherTests(unittest.TestCase):
    def test_relative_source_with_cdpath_survives_directory_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            launcher = Path(__file__).with_name("codex-hold.bash")
            (project / launcher.name).write_bytes(launcher.read_bytes())
            (project / "codex-monitor.py").write_text("print('helper reached')\n")
            result = subprocess.run([
                "bash", "--noprofile", "--norc", "-c",
                'CDPATH="$PWD"; source project/codex-hold.bash; cd /; codex-hold',
            ], cwd=root, capture_output=True, text=True, timeout=5)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("helper reached\n", result.stdout)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "desktop-user"
        self.home.mkdir()
        self.state = Path(directory.name) / "state"
        self.codex = self.home / ".codex"
        self.destination = self.home / ".local/lib/codex-turn-hold"
        for replacement in (
            patch.object(installer.Path, "home", return_value=self.home),
            patch.object(installer.os, "getuid", return_value=1000),
            patch.object(installer.importlib.util, "find_spec", return_value=object()),
            patch.dict(os.environ, {"CODEX_HOME": str(self.codex), "XDG_STATE_HOME": str(self.state)}),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def install(self):
        with redirect_stdout(io.StringIO()):
            installer.install()
        return sorted((self.state / "codex-turn-hold/backups").iterdir())[-1]

    def test_first_install_and_repeat_preserve_settings(self):
        first = self.install()
        manifest = json.loads((first / "manifest.json").read_text())
        self.assertTrue(all(not entry["existed"] for entry in manifest["files"].values()))
        self.assertFalse(self.codex.exists())
        bashrc = (self.home / ".bashrc").read_bytes()
        second = self.install()
        self.assertNotEqual(first, second)
        self.assertEqual(bashrc, (self.home / ".bashrc").read_bytes())
        self.assertEqual(bashrc, (second / "bashrc").read_bytes())

    def test_migration_preserves_originals_and_file_permissions(self):
        self.codex.mkdir()
        self.destination.mkdir(parents=True)
        originals = {
            self.home / ".bashrc": (installer.LEGACY_FUNCTION, 0o640),
            self.codex / "config.toml": (f'notify = ["bash", "{self.codex}/turn-done.sh"]\nmodel = "example"\n', 0o600),
            self.destination / "codex-monitor.py": ("# previous helper\n", 0o640),
            self.destination / "codex-hold.bash": ("# previous launcher\n", 0o600),
        }
        for path, (text, mode) in originals.items():
            path.write_text(text)
            path.chmod(mode)
        backup = self.install()
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertTrue(manifest["removed_notify"])
        for name, entry in manifest["files"].items():
            path = Path(entry["path"])
            text, mode = originals[path]
            self.assertEqual(text, (backup / name).read_text())
            self.assertEqual(mode, path.stat().st_mode & 0o777, path)
            self.assertEqual(mode, (backup / name).stat().st_mode & 0o777)
        self.assertEqual('model = "example"\n', (self.codex / "config.toml").read_text())

    def test_custom_bashrc_refuses_install_before_writing(self):
        bashrc = self.home / ".bashrc"
        original = "codex-hold( ) { echo custom; }\n"
        bashrc.write_text(original)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(original, bashrc.read_text())
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.state.exists())

    def test_installed_helper_symlink_is_not_replaced(self):
        self.destination.mkdir(parents=True)
        target = self.home / "custom-helper.py"
        target.write_text("# custom\n")
        helper = self.destination / "codex-monitor.py"
        helper.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.install()
        self.assertTrue(helper.is_symlink())
        self.assertFalse((self.home / ".bashrc").exists())

    def test_missing_source_prevents_partial_helper_update(self):
        self.destination.mkdir(parents=True)
        helper = self.destination / "codex-monitor.py"
        helper.write_bytes(b"# previous helper\n")
        source = Path(__file__).with_name("codex-hold.bash")
        read_bytes = Path.read_bytes

        def read(path):
            if path == source:
                raise FileNotFoundError(str(path))
            return read_bytes(path)

        with patch.object(Path, "read_bytes", read), self.assertRaises(FileNotFoundError):
            self.install()
        self.assertEqual(b"# previous helper\n", helper.read_bytes())

    def test_failed_atomic_replace_keeps_original_and_removes_temporary(self):
        original = self.home / "original"
        original.write_bytes(b"unchanged")
        with patch.object(installer.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                installer.atomic_write(original, b"replacement", 0o600)
        self.assertEqual(b"unchanged", original.read_bytes())
        self.assertEqual([original], list(self.home.iterdir()))


class ProcessTests(unittest.TestCase):
    def test_real_signals_cleanup_and_duplicate_process_is_rejected(self):
        # Real signals and flock, with fake Codex/GNOME adapters in the child only.
        runner = '''
from pathlib import Path
import sys
from test_monitor import monitor, Inhibitor
lock, events = map(Path, sys.argv[1:])
def record(event):
    with events.open("a") as stream:
        stream.write(event + "\\n")
class LocalInhibitor(Inhibitor):
    def __init__(self, *_):
        super().__init__()
    def acquire(self):
        super().acquire()
        record("acquire")
    def release(self):
        super().release()
        record("release")
class LocalClient:
    path = "/fixture"
    def __init__(self, *_):
        pass
    def check(self, _):
        return monitor.Snapshot(1, 1)
    def close(self):
        pass
monitor.dependency = lambda *_: None
monitor.CodexClient = LocalClient
monitor.GnomeInhibitor = LocalInhibitor
monitor.lock_path = lambda: lock
sys.exit(monitor.main([]))
'''
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as directory:
                lock, events = (Path(directory) / name for name in ("lock", "events"))
                command = [sys.executable, "-B", "-c", runner, str(lock), str(events)]
                process = subprocess.Popen(command, cwd=Path(__file__).parent,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not events.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(events.exists(), "Child never acquired its fake inhibitor")
                    duplicate = subprocess.run(command, cwd=Path(__file__).parent,
                                               capture_output=True, text=True, timeout=5)
                    self.assertEqual(1, duplicate.returncode)
                    self.assertIn("already running", duplicate.stderr)
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(128 + signum, process.returncode, stderr)
                    self.assertIn("Stopped", stdout)
                    self.assertEqual(["acquire", "release"], events.read_text().splitlines())
                    with monitor.InstanceLock(lock):
                        pass
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
