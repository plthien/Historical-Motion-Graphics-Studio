from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.modules.v4.windows_job_object import WindowsJobObject


class HostOwnedProcess:
    """Own a subprocess for the lifetime of the current backend host.

    On Windows, the child is assigned to a Job Object with KILL_ON_JOB_CLOSE.
    If the backend host exits unexpectedly, Windows closes the job handle and
    terminates any remaining child process tree. Non-Windows hosts keep the same
    small interface and fall back to ordinary root-process termination.
    """

    def __init__(self, process: subprocess.Popen[bytes], *, job: Any | None = None) -> None:
        self.process = process
        self._job = job
        self._released = False

    @classmethod
    def start(
        cls,
        command: list[str],
        *,
        cwd: Path,
        stdout: Any,
        stderr: Any,
        bufsize: int,
        env: dict[str, str],
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        job_factory: Callable[[], Any] = WindowsJobObject,
        use_job_object: bool | None = None,
    ) -> "HostOwnedProcess":
        process = popen_factory(
            command,
            cwd=cwd,
            shell=False,
            stdout=stdout,
            stderr=stderr,
            bufsize=bufsize,
            env=env,
        )
        job = None
        should_use_job = os.name == "nt" if use_job_object is None else use_job_object
        if should_use_job:
            try:
                job = job_factory()
                job.assign_process(process)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=5.0)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                raise
        return cls(process, job=job)

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def owns_process_tree(self) -> bool:
        return self._job is not None

    @property
    def released(self) -> bool:
        return self._released

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout=timeout)

    def terminate_tree(self, *, exit_code: int = 1) -> None:
        if self._job is not None:
            self._job.terminate(exit_code=exit_code)
            return
        self.process.terminate()

    def kill_root(self) -> None:
        self.process.kill()

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        job = self._job
        self._job = None
        if job is not None:
            job.close()
