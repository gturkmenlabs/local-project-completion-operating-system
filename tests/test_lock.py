import os
import time

import pytest

from altai.memory import LOCK_FILENAME, init_workspace, state_lock


def _lock_path(tmp_path):
    return init_workspace(tmp_path) / LOCK_FILENAME


def test_lock_is_created_and_released(tmp_path):
    path = _lock_path(tmp_path)
    with state_lock(tmp_path):
        assert path.exists()
    assert not path.exists()


def test_lock_from_a_dead_process_is_broken_quickly(tmp_path):
    """A leaked lock (crash, or a filesystem that refused the unlink) must not
    stall every later command until the 15-minute stale timeout."""
    path = _lock_path(tmp_path)
    dead_pid = _find_unused_pid()
    path.write_text(f"{dead_pid}:0", encoding="ascii")
    os.utime(path, (time.time() - 5, time.time() - 5))

    started = time.monotonic()
    with state_lock(tmp_path):
        pass
    assert time.monotonic() - started < 5, "should not have waited for the stale timeout"


def test_live_lock_is_not_stolen(tmp_path):
    path = _lock_path(tmp_path)
    path.write_text(f"{os.getpid()}:0", encoding="ascii")
    os.utime(path, (time.time() - 5, time.time() - 5))
    with pytest.raises(TimeoutError):
        with _short_timeout():
            with state_lock(tmp_path):
                pass
    path.unlink()


def test_unparseable_lock_is_treated_as_live(tmp_path):
    path = _lock_path(tmp_path)
    path.write_text("garbage", encoding="ascii")
    os.utime(path, (time.time() - 5, time.time() - 5))
    with pytest.raises(TimeoutError):
        with _short_timeout():
            with state_lock(tmp_path):
                pass
    path.unlink()


class _short_timeout:
    """Shrink the acquisition timeout so the test stays fast."""

    def __enter__(self):
        from altai import memory

        self._original = memory.LOCK_TIMEOUT_SECONDS
        memory.LOCK_TIMEOUT_SECONDS = 0.3
        return self

    def __exit__(self, *exc):
        from altai import memory

        memory.LOCK_TIMEOUT_SECONDS = self._original
        return False


def _find_unused_pid() -> int:
    for candidate in range(300000, 400000):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except OSError:
            continue
    pytest.skip("no free PID found")
