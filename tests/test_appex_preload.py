"""The LD_PRELOAD shim that appends Chromium switches to WeChat's webview.

The webview verifies certificates against a root store compiled into its own binary, so
the only way in is to append --ignore-certificate-errors-spki-list to its argv at exec
time (see scripts/appex-flags-preload.c). These tests build the shim and exercise it
against a stand-in binary; no WeChat and no Flatpak needed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "appex-flags-preload.c"
FLAGS = "--ignore-certificate-errors-spki-list=AAAA --allow-insecure-localhost"


@pytest.fixture(scope="module")
def shim(tmp_path_factory):
    """Compile the shim once; skip the module when there is no compiler."""
    compiler = shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        pytest.skip("no C compiler available")
    out = tmp_path_factory.mktemp("shim") / "libwx-dlh-appex-flags.so"
    build = [compiler, "-O1", "-fPIC", "-shared", "-Wall", "-Werror"]
    subprocess.run([*build, "-o", str(out), str(SOURCE), "-ldl"], check=True, capture_output=True)
    return out


def run_through_exec(shim, program: Path, argv: list[str], **env_overrides) -> str:
    """Run `env <program> <argv...>`, so the shim sees a real execvp of `program`."""
    env = {
        "PATH": "/usr/bin:/bin",
        "LD_PRELOAD": str(shim),
        "WX_DLH_APPEX_FLAGS": FLAGS,
    }
    env.update({k: v for k, v in env_overrides.items() if v is not None})
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
    done = subprocess.run(
        ["env", str(program), *argv], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def echo_as(tmp_path):
    """A copy of /bin/echo under an arbitrary program name."""

    def make(name: str) -> Path:
        target = tmp_path / name
        shutil.copy(shutil.which("echo"), target)
        return target

    return make


def test_target_gets_the_flags_appended(shim, echo_as):
    out = run_through_exec(shim, echo_as("WeChatAppEx"), ["hello"])
    assert out == f"hello {FLAGS}"


def test_other_programs_are_left_alone(shim, echo_as):
    assert run_through_exec(shim, echo_as("wechat"), ["hello"]) == "hello"


def test_a_flag_already_on_the_command_line_is_not_duplicated(shim, echo_as):
    """Chromium re-execs itself for child processes; switches must not stack up."""
    out = run_through_exec(
        shim, echo_as("WeChatAppEx"), ["--ignore-certificate-errors-spki-list=KEEP"]
    )
    assert out.count("--ignore-certificate-errors-spki-list") == 1
    assert "KEEP" in out
    assert "AAAA" not in out
    assert "--allow-insecure-localhost" in out


def test_no_flags_configured_means_no_rewrite(shim, echo_as):
    out = run_through_exec(shim, echo_as("WeChatAppEx"), ["hello"], WX_DLH_APPEX_FLAGS=None)
    assert out == "hello"


def test_the_target_program_name_is_configurable(shim, echo_as):
    out = run_through_exec(shim, echo_as("Radium"), ["hello"], WX_DLH_APPEX_TARGET="Radium")
    assert out == f"hello {FLAGS}"
