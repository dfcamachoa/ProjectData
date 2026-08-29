"""Minimal test runner so the suite runs without pytest installed.

Provides a tiny `pytest` shim (only `raises`) and executes every test_* function
in the test modules. In a normal environment just run `pytest` instead.
"""
import contextlib
import sys
import traceback
import types


# --- tiny pytest shim (only what the suite uses) ---------------------------
def _make_pytest_shim():
    m = types.ModuleType("pytest")

    @contextlib.contextmanager
    def raises(exc):
        try:
            yield
        except exc:
            return
        else:
            raise AssertionError(f"DID NOT RAISE {exc!r}")

    m.raises = raises
    return m


sys.modules.setdefault("pytest", _make_pytest_shim())

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import importlib

MODULES = ["tests.test_header"]


def main() -> int:
    passed = failed = 0
    failures = []
    for modname in MODULES:
        mod = importlib.import_module(modname)
        for name in sorted(vars(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"  PASS  {modname}::{name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                failures.append((modname, name, e, traceback.format_exc()))
                print(f"  FAIL  {modname}::{name}  -> {e}")

    print(f"\n{passed} passed, {failed} failed")
    for modname, name, e, tb in failures:
        print(f"\n===== {modname}::{name} =====\n{tb}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
