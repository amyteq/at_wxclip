"""Run the hook script's own checks, when node is available.

The script is the one part of the capture path Python cannot exercise: it lives in the
page. `tools/check-inject.mjs` drives it under stubbed browser globals and asserts, among
other things, that a 64-bit decode_key survives.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "tools" / "check-inject.mjs"


def test_hook_script_checks_pass():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed - run tools/check-inject.mjs by hand")
    result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed" in result.stdout
