"""
Windows-only workaround for a third-party gltest bug.

gltest.direct.loader._inject_message_to_fd0 (gltest 0.29.2) creates a temp
file, dup2()s it onto fd 0, then calls os.unlink() on it in a finally block
while the handle is still open. That is legal on POSIX but raises
WinError 32 on Windows, so every direct-mode deploy fails before any
contract code runs.

We suppress that one os.unlink call for the duration of the single
_inject_message_to_fd0 invocation, then restore os.unlink immediately.
This is purely a test-harness shim for the local environment; it is not
contract logic. The patch is applied only when:

  - the platform is Windows (POSIX does not need it), and
  - gltest.direct.loader still exposes _inject_message_to_fd0 and that
    function still contains an os.unlink call (a future gltest that
    already fixes the bug, or renames/removes the function, is skipped).

Any unexpected failure while installing the shim is swallowed so this
conftest can never crash a run on a machine that does not need it.
"""

import os
import sys
import inspect


def _install_windows_fd0_workaround() -> None:
    if sys.platform != "win32":
        return

    try:
        from gltest.direct import loader

        original = loader._inject_message_to_fd0
        source = inspect.getsource(original)
    except (ImportError, AttributeError, OSError, TypeError):
        # gltest version without this function (or not importable) -> no-op.
        return

    # If a future gltest no longer unlinks the temp file here, the bug is
    # already fixed and patching would be pointless.
    if "os.unlink" not in source and "unlink(" not in source:
        return

    def _patched(vm):
        real_unlink = os.unlink
        os.unlink = lambda path, *args, **kwargs: None  # type: ignore[assignment]
        try:
            return original(vm)
        finally:
            os.unlink = real_unlink

    try:
        loader._inject_message_to_fd0 = _patched
    except Exception:
        # Never let the workaround itself break the test session.
        return


_install_windows_fd0_workaround()
