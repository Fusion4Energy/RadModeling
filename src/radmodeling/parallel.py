"""Worker processes for the commands that repeat independent work on a model: one
task per instance (``compare``, ``atlas``) or per slice (``atlas``).

OCP shapes cannot be sent to other processes, so every worker loads the model itself
with a picklable ``load`` function, once, and then runs tasks on it. Workers are
spawned, not forked, on every platform: Windows can only spawn, and forking a process
that runs OCCT and the threads of ``rich`` is unsafe. Each worker runs numpy on a
single thread, as the workers already use the cores.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor
from typing import Any

# Thread pools of the numerical libraries, read when numpy is loaded
_THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

_state: Any = None  # what the load function of this worker returned


def available_cpus() -> int:
    if sys.platform == "linux":  # the CPUs this process may use (containers, taskset)
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def default_jobs() -> int:
    """Workers used by default: each one holds a copy of the model in memory."""
    return min(available_cpus(), 8)


def _start(load: Callable[[], Any]) -> None:
    global _state
    _state = load()


def _run(task: tuple[Callable[[Any, Any], Any], Any]) -> Any:
    function, item = task
    return function(_state, item)


class Workers[S]:
    """Runs ``function(state, item)`` for many items, on ``jobs`` processes.

    ``state`` is the model loaded in this process: with a single job, or a single
    item, the tasks run here on it without starting any process. Otherwise the
    processes are started at the first ``map`` and each gets its state from
    ``load()``. Functions and items must be picklable: module-level functions or
    methods of the state's class (``Atlas.draw``).
    """

    def __init__(self, state: S, load: Callable[[], S], jobs: int):
        self.state = state
        self.load = load
        self.jobs = max(jobs, 1)
        self._executor: Executor | None = None
        self._environment: dict[str, str | None] = {}

    def map[T, R](
        self, function: Callable[[S, T], R], items: Sequence[T]
    ) -> Iterator[R]:
        """The results of every item, in order."""
        if self.jobs == 1 or len(items) <= 1:
            return (function(self.state, item) for item in items)
        return self._start().map(_run, [(function, item) for item in items])

    def _start(self) -> Executor:
        if self._executor is None:
            # Inherited by the processes, which may start later, so kept until close
            self._environment = {k: os.environ.get(k) for k in _THREAD_VARIABLES}
            os.environ.update(dict.fromkeys(_THREAD_VARIABLES, "1"))
            self._executor = ProcessPoolExecutor(
                self.jobs,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_start,
                initargs=(self.load,),
            )
        return self._executor

    def close(self, wait: bool = True) -> None:
        """Stop the processes: after the running tasks, or at once without ``wait``."""
        if self._executor is not None:
            self._executor.shutdown(wait=wait, cancel_futures=True)
            self._executor = None
            for key, value in self._environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def __enter__(self) -> Workers[S]:
        return self

    def __exit__(self, exc_type: type | None, *exc_info: object) -> None:
        self.close(wait=exc_type is None)
