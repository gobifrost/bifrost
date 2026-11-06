"""Exercise the real launchers with a recording Docker boundary, without a stack."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['MOCK_STATE'])
with (root / 'commands').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
running = root / 'running'
if args[:2] == ['container', 'inspect']:
    sys.exit(1)
if args[0] == 'ps':
    if running.exists(): print('session-api')
elif args[0] == 'wait':
    (root / 'ready').touch()
    if not os.environ.get('MOCK_SERVICE_EXIT'): signal.pause()
elif args[0] == 'port':
    print('127.0.0.1:32123')
elif args[0] == 'inspect' and '{{.State.Health.Status}}' in args:
    print('healthy')
elif args[0] == 'compose':
    if 'down' in args:
        running.unlink(missing_ok=True)
        sys.exit(int(os.environ.get('MOCK_DOWN_STATUS', '0')))
    elif 'up' in args:
        running.touch()
        sys.exit(int(os.environ.get('MOCK_BOOT_STATUS', '0')))
    elif 'ps' in args and running.exists():
        if '--services' in args: print('api')
        elif '-q' in args: print('session-api')
        else: print('running')
    elif 'logs' in args:
        if '--follow' in args: signal.pause()
        else: print('preserved service diagnostics')
    elif 'run' in args:
        if 'pytest' in args:
            (root / 'ready').touch()
            if os.environ.get('MOCK_BLOCK_RUN'): signal.pause()
            sys.exit(int(os.environ.get('MOCK_RUN_STATUS', '0')))
elif args[:2] == ['image', 'ls']:
    print('bifrost-test-api-dev:' + os.environ['COMPOSE_PROJECT_NAME'])
'''


class StackLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="bifrost-lifecycle-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        project_hash = hashlib.sha256(str(self.root).encode()).hexdigest()[:8]
        for kind in ("test", "debug"):
            self.addCleanup(
                shutil.rmtree, f"/tmp/bifrost-bifrost-{kind}-{project_hash}", True,
            )
        for name in (
            "test.sh", "debug.sh", "scripts/lib/test_helpers.sh",
            "scripts/lib/stack_lifecycle.sh",
        ):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Lifecycle Test",
             "-c", "user.email=lifecycle@example.test", "commit", "--allow-empty",
             "--no-gpg-sign", "--no-verify", "-qm", "Lifecycle fixture"], check=True,
        )
        template = self.root / "scripts/stack_template_init.sh"
        template.write_text("#!/bin/sh\nexit 0\n")
        template.chmod(0o755)
        (self.root / "bin").mkdir()
        docker = self.root / "bin/docker"
        docker.write_text(DOCKER)
        docker.chmod(0o755)
        npm = self.root / "bin/npm"
        npm.write_text("#!/bin/sh\nexit 0\n")
        npm.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.root}/bin:{os.environ['PATH']}",
            "MOCK_STATE": str(self.root),
            "BIFROST_FORCE_PORT": "1",
        }

    def run_script(self, script, *args, **env):
        return subprocess.run(
            ["bash", str(self.root / script), *args], env={**self.env, **env},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20,
        )

    def commands(self):
        path = self.root / "commands"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_cleanup(self):
        commands = self.commands()
        downs = [c for c in commands if "down" in c]
        self.assertEqual(len(downs), 1, commands)
        self.assertIn("--volumes", downs[0])
        self.assertIn("--remove-orphans", downs[0])
        self.assertIn("*", downs[0])
        logs = next(i for i, c in enumerate(commands) if "logs" in c and "--follow" not in c)
        self.assertLess(logs, commands.index(downs[0]))

    def start_script(self, script, *args, **env):
        output = tempfile.TemporaryFile(mode="w+")
        self.addCleanup(output.close)
        process = subprocess.Popen(
            ["bash", str(self.root / script), *args], env={**self.env, **env},
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )

        def stop():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)

        self.addCleanup(stop)
        deadline = time.monotonic() + 15
        while not (self.root / "ready").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                output.seek(0)
                self.fail(f"session did not become ready: {output.read()}")
            time.sleep(0.01)
        return process, output

    def test_test_success_removes_resources_and_worktree_image_tag(self):
        result = self.run_script("test.sh", "tests/unit/example.py")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_cleanup()
        commands = self.commands()
        self.assertTrue(any("up" in c for c in commands))
        image_removal = next(c for c in commands if c[:2] == ["image", "rm"])
        self.assertEqual(len(image_removal), 3)
        self.assertRegex(image_removal[2], r"^bifrost-test-api-dev:bifrost-test-[a-f0-9]{8}$")

    def test_test_failure_preserves_status_and_tears_down(self):
        result = self.run_script("test.sh", "tests/unit/example.py", MOCK_RUN_STATUS="7")
        self.assertEqual(result.returncode, 7, result.stdout)
        self.assert_cleanup()

    def test_combined_command_shares_stack_and_resets_between_suites(self):
        result = self.run_script("test.sh", "ci")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_cleanup()
        commands = self.commands()
        boots = [c for c in commands if "up" in c and "postgres" in c]
        self.assertEqual(len(boots), 1)
        self.assertEqual(result.stdout.count("Using clean state"), 1)
        self.assertEqual(result.stdout.count("State reset complete"), 1)
        self.assertTrue(any("pytest" in c for c in commands))
        self.assertTrue(any("playwright-runner" in c for c in commands))

    def test_partial_startup_failure_tears_down(self):
        result = self.run_script("test.sh", "e2e", MOCK_BOOT_STATUS="37")
        self.assertEqual(result.returncode, 37, result.stdout)
        self.assert_cleanup()
        self.assertFalse(any("pytest" in c for c in self.commands()))

    def test_quality_cleans_one_off_resources_without_starting_services(self):
        result = self.run_script("test.sh", "quality", "api")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(sum("down" in c for c in self.commands()), 1)
        self.assertFalse(any("up" in c for c in self.commands()))

    def test_removed_manual_boot_does_not_create_resources(self):
        result = self.run_script("test.sh", "stack", "up")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("Tests boot and tear down their own stack", result.stdout)
        self.assertEqual(self.commands(), [])

    def test_orphaned_running_test_runner_is_preserved(self):
        (self.root / "running").touch()
        result = self.run_script("test.sh", "tests/unit/example.py")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("a runner still owns", result.stdout)
        self.assertTrue((self.root / "running").exists())
        self.assertFalse(any("down" in c or "up" in c for c in self.commands()))

    def test_help_does_not_create_resources(self):
        result = self.run_script("test.sh", "--help")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.commands(), [])

    def test_test_signals_exit_and_clean_up(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                (self.root / "ready").unlink(missing_ok=True)
                (self.root / "commands").unlink(missing_ok=True)
                process, output = self.start_script(
                    "test.sh", "tests/unit/example.py", MOCK_BLOCK_RUN="1",
                )
                os.killpg(process.pid, sig)
                self.assertEqual(process.wait(timeout=15), 128 + sig)
                self.assert_cleanup()

    def test_debug_stays_running_until_interrupted(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                (self.root / "ready").unlink(missing_ok=True)
                (self.root / "commands").unlink(missing_ok=True)
                process, output = self.start_script("debug.sh", "up")
                self.assertIsNone(process.poll())
                os.kill(process.pid, sig)
                self.assertEqual(process.wait(timeout=15), 128 + sig)
                output.seek(0)
                self.assertIn("Press Ctrl+C", output.read())
                self.assert_cleanup()

    def test_second_debug_launcher_cannot_tear_down_owner(self):
        process, _ = self.start_script("debug.sh")
        result = self.run_script("debug.sh", "up")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("another debug session owns", result.stdout)
        self.assertFalse(any("down" in c for c in self.commands()))
        self.assertIsNone(process.poll())

    def test_explicit_debug_down_ends_owner_before_removing_resources(self):
        process, _ = self.start_script("debug.sh", "up")
        result = self.run_script("debug.sh", "down")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(process.wait(timeout=15), 143)
        self.assertFalse((self.root / "running").exists())

    def test_debug_service_exit_tears_down_the_session(self):
        result = self.run_script("debug.sh", "up", MOCK_SERVICE_EXIT="1")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assert_cleanup()

    def test_debug_startup_failure_tears_down(self):
        result = self.run_script("debug.sh", "up", MOCK_BOOT_STATUS="19")
        self.assertEqual(result.returncode, 19, result.stdout)
        self.assert_cleanup()

    def test_cleanup_failure_fails_successful_command(self):
        result = self.run_script("test.sh", "tests/unit/example.py", MOCK_DOWN_STATUS="23")
        self.assertEqual(result.returncode, 23, result.stdout)
        self.assertIn("stack cleanup failed", result.stdout)

    def test_cleanup_failure_preserves_original_failure(self):
        result = self.run_script(
            "test.sh", "tests/unit/example.py", MOCK_RUN_STATUS="7", MOCK_DOWN_STATUS="23",
        )
        self.assertEqual(result.returncode, 7, result.stdout)


if __name__ == "__main__":
    unittest.main()
