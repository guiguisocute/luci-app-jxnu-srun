import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_ROOT = os.path.join(REPO_ROOT, "root", "usr", "lib", "jxnu_srun")

if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)


import daemon
import school_runtime
import schools


class FakeRuntime(object):
    def __init__(self):
        self.calls = []
        self.extra_commands = []
        self.extra_result = None
        self.daemon_result = None

    def get_cli_commands(self):
        return list(self.extra_commands)

    def handle_cli_command(self, app_ctx, args):
        self.calls.append(("handle_cli_command", args.command))
        return self.extra_result

    def cli_status(self, app_ctx, args):
        self.calls.append(("cli_status", args.command))
        print("STATUS:%s" % app_ctx["cfg"].get("school", "default"))
        return True, 0, ""

    def cli_login(self, app_ctx, args):
        self.calls.append(("cli_login", args.command))
        return True, 0, "runtime-cli-login"

    def cli_logout(self, app_ctx, args):
        self.calls.append(("cli_logout", args.command))
        return True, 0, "runtime-cli-logout"

    def cli_relogin(self, app_ctx, args):
        self.calls.append(("cli_relogin", args.command))
        return True, 0, "runtime-cli-relogin"

    def cli_daemon(self, app_ctx, args):
        self.calls.append(("cli_daemon", args.command))
        return True, 0, "runtime-cli-daemon"

    def status(self, app_ctx):
        self.calls.append(("status", app_ctx["cfg"].get("school")))
        return True, "runtime-status"

    def daemon_before_tick(self, app_ctx, state, interval):
        self.calls.append(("daemon_before_tick", interval))
        return self.daemon_result


class SchoolRuntimeCliTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"school": "custom", "enabled": "1", "interval": "30"}
        self.runtime = FakeRuntime()
        self.app_ctx = school_runtime.build_app_context(self.cfg, runtime=self.runtime)

    def run_main(self, argv):
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["srunnet"] + argv),
            mock.patch.object(daemon, "load_config", return_value=dict(self.cfg)),
            mock.patch.object(
                school_runtime, "resolve_runtime", return_value=self.runtime
            ),
            mock.patch.object(
                school_runtime, "build_app_context", return_value=self.app_ctx
            ),
            redirect_stdout(stdout),
        ):
            try:
                daemon.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue()

    def test_bare_command_matches_status_dispatch(self):
        bare_code, bare_output = self.run_main([])
        status_code, status_output = self.run_main(["status"])

        self.assertEqual(bare_code, 0)
        self.assertEqual(status_code, 0)
        self.assertEqual(bare_output, status_output)
        self.assertIn(("status", "custom"), self.runtime.calls)
        self.assertNotIn(("cli_status", "status"), self.runtime.calls)

    def test_schools_list_keeps_metadata_shape(self):
        payload = [
            {
                "short_name": "jxnu",
                "name": "JXNU",
                "description": "desc",
                "contributors": ["a"],
                "operators": [{"id": "xn", "label": "校园网"}],
                "no_suffix_operators": ["xn"],
            }
        ]
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["srunnet", "schools"]),
            mock.patch.object(daemon, "load_config", return_value=dict(self.cfg)),
            mock.patch.object(
                school_runtime, "resolve_runtime", return_value=self.runtime
            ),
            mock.patch.object(
                school_runtime, "build_app_context", return_value=self.app_ctx
            ),
            mock.patch.object(schools, "list_schools", return_value=payload),
            redirect_stdout(stdout),
        ):
            daemon.main()

        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_schools_inspect_selected_returns_selected_runtime_metadata(self):
        inspect_payload = {
            "short_name": "custom",
            "runtime_type": "runtime_class",
            "runtime_api_version": 1,
            "source_file": "/tmp/custom.py",
            "declared_capabilities": ["cli", "daemon"],
        }
        stdout = io.StringIO()
        with (
            mock.patch.object(
                sys, "argv", ["srunnet", "schools", "inspect", "--selected"]
            ),
            mock.patch.object(daemon, "load_config", return_value=dict(self.cfg)),
            mock.patch.object(
                school_runtime, "resolve_runtime", return_value=self.runtime
            ),
            mock.patch.object(
                school_runtime, "build_app_context", return_value=self.app_ctx
            ),
            mock.patch.object(
                school_runtime, "inspect_runtime", return_value=inspect_payload
            ),
            redirect_stdout(stdout),
        ):
            daemon.main()

        self.assertEqual(json.loads(stdout.getvalue()), inspect_payload)

    def test_reserved_commands_cannot_be_replaced_by_runtime(self):
        self.runtime.extra_commands = [{"name": "status", "help": "bad"}]

        with self.assertRaisesRegex(ValueError, "reserved command"):
            self.run_main([])

    def test_runtime_cli_dispatch_requires_fixed_result_shape(self):
        self.runtime.extra_commands = [{"name": "custom", "help": "custom command"}]
        self.runtime.extra_result = (0, "bad-shape")

        with self.assertRaisesRegex(RuntimeError, "CLI contract"):
            self.run_main(["custom"])

    def test_runtime_cli_dispatch_uses_exit_code_and_message(self):
        self.runtime.extra_commands = [{"name": "custom", "help": "custom command"}]
        self.runtime.extra_result = (True, 7, "runtime custom message")

        code, output = self.run_main(["custom"])

        self.assertEqual(code, 7)
        self.assertIn("runtime custom message", output)
        self.assertIn(("handle_cli_command", "custom"), self.runtime.calls)

    def test_daemon_early_stop_requires_ok_message_tuple(self):
        self.runtime.daemon_result = (True, "stop", "bad")

        with self.assertRaisesRegex(RuntimeError, "daemon contract"):
            daemon._run_runtime_daemon_hook(self.app_ctx, {"was_online": False}, 30)

    def test_reserved_status_command_ignores_runtime_cli_hook(self):
        with mock.patch.object(daemon, "_show_status") as show_status:
            code, output = self.run_main(["status"])

        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        show_status.assert_called_once_with(self.cfg)
        self.assertNotIn(("cli_status", "status"), self.runtime.calls)

    def test_reserved_login_logout_relogin_commands_ignore_runtime_cli_hooks(self):
        with (
            mock.patch.object(
                daemon, "_runtime_cli_login", return_value=(True, 0, "core-login")
            ),
            mock.patch.object(
                daemon, "_runtime_cli_logout", return_value=(True, 0, "core-logout")
            ),
            mock.patch.object(
                daemon, "_runtime_cli_relogin", return_value=(True, 0, "core-relogin")
            ),
        ):
            login_code, login_output = self.run_main(["login"])
            logout_code, logout_output = self.run_main(["logout"])
            relogin_code, relogin_output = self.run_main(["relogin"])

        self.assertEqual((login_code, login_output.strip()), (0, "core-login"))
        self.assertEqual((logout_code, logout_output.strip()), (0, "core-logout"))
        self.assertEqual((relogin_code, relogin_output.strip()), (0, "core-relogin"))
        self.assertNotIn(("cli_login", "login"), self.runtime.calls)
        self.assertNotIn(("cli_logout", "logout"), self.runtime.calls)
        self.assertNotIn(("cli_relogin", "relogin"), self.runtime.calls)

    def test_reserved_daemon_command_ignores_runtime_cli_hook(self):
        with mock.patch.object(daemon, "run_daemon") as run_daemon:
            code, output = self.run_main(["daemon"])

        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        run_daemon.assert_called_once_with(runtime=self.runtime)
        self.assertNotIn(("cli_daemon", "daemon"), self.runtime.calls)


if __name__ == "__main__":
    unittest.main()
