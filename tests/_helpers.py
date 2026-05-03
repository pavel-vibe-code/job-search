"""Shared test helpers — import the fetch-and-diff module under a tidy name."""

import importlib.util
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(PLUGIN_ROOT, "scripts", "fetch-and-diff.py")
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_fad():
    """Load scripts/fetch-and-diff.py as a module despite the dash in the
    filename. Returns the imported module."""
    spec = importlib.util.spec_from_file_location("fad", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_script(filename: str, module_name: str = None):
    """Load any scripts/*.py module by filename. Used for scripts beyond fetch-and-diff."""
    path = os.path.join(PLUGIN_ROOT, "scripts", filename)
    spec = importlib.util.spec_from_file_location(
        module_name or filename.replace("-", "_").replace(".py", ""), path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fixture_path(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


def read_fixture(name: str, mode: str = "r") -> str:
    with open(fixture_path(name), mode) as f:
        return f.read()
