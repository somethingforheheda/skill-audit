"""Owned agent CLI process groups with cooperative cancellation and bounded cleanup."""
import os
import signal
import subprocess
import tempfile
import threading
import time


class Cancelled(Exception):
    pass


class TimedOut(Exception):
    pass


def stop_group(process, hard=False):
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL if hard else signal.SIGTERM)
        elif process.poll() is None:
            process.kill() if hard else process.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass


class Control:
    def __init__(self):
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.processes = set()

    def add(self, process):
        with self.lock:
            self.processes.add(process)
            if self.stopped.is_set():
                stop_group(process)

    def remove(self, process):
        with self.lock:
            self.processes.discard(process)

    def cancel(self):
        self.stopped.set()
        with self.lock:
            for process in self.processes:
                stop_group(process)

    def check(self):
        if self.stopped.is_set():
            raise Cancelled('用户已取消')


def execute(command, prompt, timeout, env, cwd, control=None, label='Codex'):
    control = control or Control()
    control.check()
    # The prompt goes through a file, not a pipe: communicate(input=...) retried after a timeout never finishes
    # writing or closes stdin on Python 3.9, so a CLI that starts slowly would wait for its prompt forever.
    with tempfile.TemporaryFile() as feed:
        feed.write((prompt or '').encode('utf-8'))
        feed.flush()
        feed.seek(0)
        return _run(command, feed, timeout, env, cwd, control, label)


def _run(command, feed, timeout, env, cwd, control, label):
    process = subprocess.Popen(command, stdin=feed, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env, cwd=cwd,
                               start_new_session=(os.name == 'posix'))
    control.add(process)
    start = time.monotonic()
    try:
        while True:
            control.check()
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise TimedOut(f'{label} 超时（{timeout} 秒），不会自动原样重跑此任务')
            try:
                out, err = process.communicate(timeout=min(.2, remaining))
                control.check()
                return subprocess.CompletedProcess(command, process.returncode, out, err)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        control.cancel()
        raise
    finally:
        # Kill descendants too, including plugin workers left behind after a normal exit.
        stop_group(process)
        try:
            process.communicate(timeout=.8)
        except subprocess.TimeoutExpired:
            stop_group(process, hard=True)
            try:
                process.communicate(timeout=.8)
            except subprocess.TimeoutExpired:
                # Rare inherited-pipe leaks cannot hold the CLI's worker thread forever.
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        try: stream.close()
                        except OSError: pass
        # A child may ignore TERM and redirect its pipes; always reap the owned group.
        stop_group(process, hard=True)
        control.remove(process)
