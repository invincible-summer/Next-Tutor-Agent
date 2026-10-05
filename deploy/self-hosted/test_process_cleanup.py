"""Focused launcher regressions; all processes and files live in a temporary dir."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from process_cleanup import identity, stop_trees


class ProcessCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tutor-launcher-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def launch(self, code="import time; time.sleep(60)", cwd=None, title=None):
        command = [sys.executable, "-c", code]
        if title:
            command = ["bash", "-c", 'exec -a "$1" "$2" -c "$3"',
                       "bash", title, sys.executable, code]
        proc = subprocess.Popen(command, cwd=cwd or self.root,
                                stdout=subprocess.PIPE, text=True)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
        self.addCleanup(cleanup)
        return proc

    def test_stops_owned_parent_and_child(self):
        proc = self.launch("import subprocess,sys,time; "
                           "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                           "print(p.pid,flush=True); time.sleep(60)")
        child = int(proc.stdout.readline())
        self.addCleanup(lambda: os.kill(child, signal.SIGKILL) if identity(child) else None)
        stop_trees(self.root, [proc.pid], grace=0.5)
        proc.wait(timeout=5)
        self.assertIsNone(identity(child))

    def test_does_not_stop_sibling_repository(self):
        sibling = self.root / "app-other"
        sibling.mkdir()
        root = self.root / "app"
        root.mkdir()
        proc = self.launch(cwd=sibling)
        stop_trees(root, [proc.pid], grace=0)
        self.assertIsNone(proc.poll())

    def test_escalates_only_unresponsive_owned_process(self):
        proc = self.launch("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                           "print('ready',flush=True); time.sleep(60)")
        proc.stdout.readline()
        stop_trees(self.root, [proc.pid], grace=0.1)
        self.assertEqual(proc.wait(timeout=5), -signal.SIGKILL)

    def test_saved_start_time_rejects_reused_pid_in_same_repository(self):
        replacement = self.launch()
        stamp = identity(replacement.pid)[1]
        stop_trees(self.root, [replacement.pid], grace=0,
                   expected_starts={replacement.pid: str(int(stamp) - 1)})
        self.assertIsNone(replacement.poll())

    def test_old_launcher_cleanup_keeps_replacement_port_owner(self):
        old = self.launch()
        old.terminate()
        old.wait(timeout=5)
        replacement = self.launch("import socket,time; s=socket.socket(); "
                                  "s.bind(('127.0.0.1',0)); s.listen(); "
                                  "print(s.getsockname()[1],flush=True); time.sleep(60)")
        port = int(replacement.stdout.readline())
        repo = Path(__file__).resolve().parents[2]
        script = (repo / "scripts/dev/start.sh").read_text()
        cleanup = script[script.index("cleanup() {"):script.index("\npick_port() {")]
        env = {**os.environ, "ROOT": str(repo), "PYTHON_BIN": sys.executable,
               "BACK_PID": str(old.pid), "FRONT_PID": "", "VOICE_PID": "",
               "BACK_PORT": str(port), "FRONT_PORT": "", "VOICE_PORT": ""}
        subprocess.run(["bash", "-c", cleanup + "\ncleanup"], env=env, check=True)
        self.assertIsNone(replacement.poll())
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass

    def test_repeated_interrupt_during_cleanup_finishes_without_traceback(self):
        proc = self.launch("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                           "print('ready',flush=True); time.sleep(60)")
        proc.stdout.readline()
        script = Path(__file__).with_name("process_cleanup.py").resolve()
        cleaner = subprocess.Popen([sys.executable, str(script), str(self.root), str(proc.pid)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: cleaner.kill() if cleaner.poll() is None else None)
        # Wait until its ignored handlers are installed, without relying on timing.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = Path(f"/proc/{cleaner.pid}/status").read_text()
            ignored = next(line.split()[1] for line in status.splitlines() if line.startswith("SigIgn:"))
            if int(ignored, 16) & (1 << (signal.SIGINT - 1)):
                break
            time.sleep(0.01)
        else:
            self.fail("cleanup signal handler was not installed")
        cleaner.send_signal(signal.SIGINT)
        cleaner.send_signal(signal.SIGINT)
        cleaner.send_signal(signal.SIGTERM)
        stdout, stderr = cleaner.communicate(timeout=8)
        self.assertEqual(cleaner.returncode, 0, stderr)
        self.assertNotIn("KeyboardInterrupt", stdout + stderr)
        self.assertEqual(proc.wait(timeout=5), -signal.SIGKILL)

    def test_noninteractive_launch_survives_external_parent_group_signals(self):
        repo = Path(__file__).resolve().parents[2]
        source = (repo / "scripts/dev/start.sh").read_text()
        prefix = source[:source.index("# This deployment runs")]
        # The launcher computes ROOT as its own location's ../.. (it lives at
        # scripts/dev/); the temp copy sits at the sandbox root, so point ROOT
        # at the sandbox itself for the detached re-exec.
        prefix = prefix.replace('ROOT="$(cd "$(dirname "$0")/../.." && pwd)"',
                                'ROOT="$(cd "$(dirname "$0")" && pwd)"')
        # Exercise the real detach entry point with a synthetic worker. No real
        # backend, ports, credentials, runtime storage or browser is involved.
        launcher = self.root / "start.sh"
        launcher.write_text(prefix + f'''exec {sys.executable} -c '
import os,time
from pathlib import Path
Path("{self.root}/worker.pid").write_text(str(os.getpid()))
time.sleep(60)
'
''')
        launcher.chmod(0o700)
        parent = subprocess.Popen(["bash", "-c", '"$1"; sleep 60', "bash", str(launcher)],
                                  cwd=self.root, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, start_new_session=True)
        self.addCleanup(lambda: parent.kill() if parent.poll() is None else None)
        deadline = time.monotonic() + 3
        while not (self.root / "worker.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.root / "worker.pid").exists())
        worker = int((self.root / "worker.pid").read_text())
        self.addCleanup(lambda: os.kill(worker, signal.SIGKILL) if identity(worker) else None)
        self.assertNotEqual(os.getsid(worker), os.getsid(parent.pid))
        os.killpg(parent.pid, signal.SIGINT)
        os.killpg(parent.pid, signal.SIGHUP)
        parent.communicate(timeout=3)
        self.assertIsNotNone(identity(worker))
        stop_trees(self.root, [worker], grace=0.1)

    def test_stop_does_not_scan_unregistered_services_in_repository(self):
        repo = Path(__file__).resolve().parents[2]
        source = (repo / "scripts/dev/start.sh").read_text()
        stop = source[source.index("stop_all() {"):source.index('\ncase "${1:-all}"')]
        # stop_all ends by clearing the /tmp pid markers through this helper;
        # slice it out too so the isolated shell defines everything it calls.
        helper_fn = source[source.index("write_runtime_file() {"):
                           source.index("\n\n# start.sh starts the latest")]
        unrelated = self.launch("print('ready',flush=True); import time; time.sleep(60)", title="next-server")
        unrelated.stdout.readline()
        # Former command matcher would have selected this same-cwd process.
        env = {**os.environ, "ROOT": str(self.root), "PYTHON_BIN": sys.executable,
               "RUNTIME_DIR": str(self.root / ".runtime")}
        helper = self.root / "deploy" / "self-hosted"
        helper.mkdir(parents=True)
        helper.joinpath("process_cleanup.py").write_text((repo / "deploy/self-hosted/process_cleanup.py").read_text())
        # Override legacy PID-file lookup in this isolated shell; it must never
        # interact with the actual launcher's /tmp/edu_* files.
        script = stop.replace('"/tmp/edu_${name}_pid"', '"$ROOT/legacy_${name}_pid"')
        script = script.replace(': > /tmp/edu_', ': > "$ROOT"/edu_')
        script = script.replace('write_runtime_file /tmp/edu_',
                                'write_runtime_file "$ROOT"/edu_')
        script = helper_fn + "\n" + script
        subprocess.run(["bash", "-c", script + "\nstop_all"], env=env, check=True)
        self.assertIsNone(unrelated.poll())

    def test_failed_child_does_not_stop_other_live_service(self):
        repo = Path(__file__).resolve().parents[2]
        source = (repo / "scripts/dev/start.sh").read_text()
        monitor = source[source.index("# Wait only for owned servers"):]
        script = """set -e
BACK_PID=''; FRONT_PID=''; VOICE_PID=''
(sleep 0.1; exit 7) & BACK_PID=$!
(sleep 0.3; printf 'still-alive\\n') & FRONT_PID=$!
""" + monitor
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=7", result.stdout)
        self.assertIn("still-alive", result.stdout)


if __name__ == "__main__":
    unittest.main()
