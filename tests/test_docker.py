"""The container files, checked without Docker.

`make test` needs no Docker daemon, so building and running the image stays a manual check
(docs/docker.md lists what was run). What these cover is what goes stale silently: a file
the wheel needs that the .dockerignore allowlist leaves out fails only at
`docker compose build`, a secret that a later negation lets back into the build context,
the entrypoint's choice of uid (who owns the files it writes into the host's directories)
and its clearing of the proxy variables a Docker client injects (whether a download still
goes direct).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "docker-entrypoint.sh"
#: The image's WX_DLH_<key>_DIR settings and the /data/<name> directory each points at.
DATA_DIRS = {"CA": "ca", "CAPTURE": "captures", "DOWNLOAD": "downloads", "PROCESS": "processed"}
#: Secrets that must stay out of the build context even below an allowlisted directory.
SECRET_PATTERNS = ("**/.env*", "**/*.pem", "**/*.cer", "**/*.crt", "**/*.key", "**/*.p12")
#: What a native run leaves under wx_dlh/ when a directory setting points at the package
#: tree (`WX_DLH_CAPTURE_DIR=wx_dlh/captures`), plus the usual local debris. Git ignores
#: every one of them, so the build context is the only thing keeping them off the builder.
RUNTIME_OUTPUT = (
    "wx_dlh/captures/events.jsonl",
    "wx_dlh/captures/flows-20261012.mitm",
    "wx_dlh/captures/pages/channels.html",
    "wx_dlh/downloads/clip.mp4",
    "wx_dlh/processed/clip/shot-001.png",
    "wx_dlh/temp/scratch.txt",
    "wx_dlh/ca/mitmproxy-ca.pem",
    "wx_dlh/.env.local",
    "wx_dlh/__pycache__/config.cpython-312.pyc",
)
#: What a Docker client with `proxies` configured injects into every container, both cases.
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def dockerignore() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def allowlisted() -> set[str]:
    return {line[1:].rstrip("/") for line in dockerignore() if line.startswith("!")}


def tracked(path: str) -> list[str]:
    """Everything Git has under `path`.

    A working checkout holds more than this, and that untracked rest - scratch copies,
    captures, a CA someone pointed at the package - is what must not reach the builder.
    """
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("needs a Git checkout")
    out = subprocess.run(
        [git, "-C", str(ROOT), "ls-files", "-z", "--", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in out.stdout.split("\0") if name]


def as_regex(pattern: str) -> re.Pattern[str]:
    """One .dockerignore pattern: `*` and `?` stop at a separator, `**` crosses them."""
    globs = (("**/", "(?:.*/)?"), ("**", ".*"), ("*", "[^/]*"), ("?", "[^/]"))
    out, rest = "", pattern.rstrip("/")
    while rest:
        for prefix, translation in globs:
            if rest.startswith(prefix):
                out, rest = out + translation, rest[len(prefix) :]
                break
        else:
            out, rest = out + re.escape(rest[0]), rest[1:]
    return re.compile(f"{out}$")


def context_takes(path: str) -> bool:
    """Whether `path` reaches the builder.

    Docker takes the last pattern matching the path *or any directory above it*: the
    top-level `*` therefore drops whole trees, and only a later negation naming something
    inside brings part of one back - which is how `!web/inject.js` gets in while
    `web/README.md` does not. Checked against a real `docker build` (docs/docker.md).
    """
    parts = path.split("/")
    candidates = ["/".join(parts[: depth + 1]) for depth in range(len(parts))]
    verdict = True
    for pattern in dockerignore():
        regex = as_regex(pattern.removeprefix("!"))
        if any(regex.match(candidate) for candidate in candidates):
            verdict = pattern.startswith("!")
    return verdict


def wheel_inputs() -> list[str]:
    """Every path hatchling reads to build the wheel, as pyproject.toml names it."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    paths = [*wheel.get("packages", []), *wheel.get("force-include", {})]
    project = pyproject["project"]
    if isinstance(project.get("readme"), str):
        paths.append(project["readme"])
    paths.extend(project.get("license-files", []))
    return paths


def test_the_build_context_holds_everything_the_wheel_is_built_from():
    missing = [path for path in wheel_inputs() if path not in allowlisted()]
    assert not missing, f"add to .dockerignore as !<path>: {missing}"


def test_the_build_context_holds_the_entrypoint():
    assert "scripts/docker-entrypoint.sh" in allowlisted()


def test_the_build_context_takes_the_package_and_nothing_else_below_it():
    """`*` only matches the top level, so a bare `!wx_dlh/` hands the builder the whole
    package tree - including the git-ignored output a native run can leave in it.

    The allowlist is narrowed to the modules instead, which is only enough while the
    package stays pure Python: a data file the wheel needs fails here rather than at
    `docker compose build`, and anything else is excluded by construction.
    """
    package = tracked("wx_dlh")
    assert package, "expected wx_dlh/ to be tracked"
    refused = [path for path in package if not context_takes(path)]
    assert not refused, f"the wheel needs these - widen .dockerignore: {refused}"
    rode_in = [path for path in RUNTIME_OUTPUT if context_takes(path)]
    assert not rode_in, f"git-ignored runtime output reaches the builder: {rode_in}"


def test_no_secret_rides_into_the_build_context_on_an_allowlisted_directory():
    """Docker applies the last matching pattern, which is why these come after every
    negation: a CA directory or a .env below an allowlisted one is excluded again.

    Redundant while the negations stay as narrow as they are now - this pins that a
    broader one added later still cannot re-admit them.
    """
    patterns = dockerignore()
    last_negation = max(index for index, line in enumerate(patterns) if line.startswith("!"))
    for pattern in SECRET_PATTERNS:
        assert pattern in patterns, f"add to .dockerignore: {pattern}"
        assert patterns.index(pattern) > last_negation, f"a negation overrides {pattern}"


def test_compose_mounts_the_directories_the_image_points_wx_dlh_at():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    env = dict(re.findall(r"(WX_DLH_\w+_DIR)=(\S+)", dockerfile))
    mounted = set(re.findall(r":(/data/\w+)\s*$", compose, flags=re.MULTILINE))
    assert env == {f"WX_DLH_{key}_DIR": f"/data/{name}" for key, name in DATA_DIRS.items()}
    assert mounted == set(env.values())


def test_compose_hands_the_entrypoint_the_outbound_proxy_setting():
    """The one environment block every service inherits, so `cli` and `capture` get it too."""
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "WX_DLH_OUTBOUND_PROXY: ${WX_DLH_OUTBOUND_PROXY:-}" in compose


# -- the entrypoint, with id / stat / chown / setpriv replaced by recording stubs ---------

# `stat -c %u DIR` answers from STUB_U_<basename>, %g from STUB_G_<basename>; 0 (root,
# i.e. a directory Docker just created) when unset.
_LOG_PROXY_ENV = 'env | grep -iE "^(http|https|all|no)_proxy=" | sed "s/^/env /" >> "$STUB_LOG"'
STUBS = {
    "id": 'echo "${STUB_ID:-0}"',
    "stat": """fmt="$2"; name="$(basename "$3")"
case "$fmt" in
  %u) eval "echo \\${STUB_U_$name:-0}" ;;
  %g) eval "echo \\${STUB_G_$name:-0}" ;;
esac""",
    "chown": 'echo "chown $*" >> "$STUB_LOG"',
    # Both ways out of the entrypoint. Each logs the proxy environment it was handed, as
    # `env <NAME>=<value>` lines before the call itself.
    "setpriv": f'{_LOG_PROXY_ENV}\necho "setpriv $*" >> "$STUB_LOG"',
    "wx-dlh": f'{_LOG_PROXY_ENV}\necho "wx-dlh $*" >> "$STUB_LOG"',
}


@pytest.fixture
def entrypoint(tmp_path: Path):
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("needs a POSIX sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    data = tmp_path / "data"
    for name in DATA_DIRS.values():  # the mounts: Docker has made them by the time we run
        (data / name).mkdir(parents=True)

    def run(*args: str, **env: str) -> list[str]:
        base = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "STUB_LOG": str(log),
            **{f"WX_DLH_{key}_DIR": str(data / name) for key, name in DATA_DIRS.items()},
        }
        log.write_text("", encoding="utf-8")
        result = subprocess.run(
            [sh, str(ENTRYPOINT), *args],
            env={**base, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return log.read_text(encoding="utf-8").splitlines()

    return run


def setpriv(uid: int, gid: int, *args: str) -> str:
    return f"setpriv --reuid={uid} --regid={gid} --clear-groups -- wx-dlh {' '.join(args)}"


def chowned(calls: list[str]) -> dict[str, str]:
    """Directory name -> the owner it was handed to."""
    return {Path(c.split()[2]).name: c.split()[1] for c in calls if c.startswith("chown ")}


def proxy_env(calls: list[str]) -> dict[str, str]:
    """The proxy variables left in the environment wx-dlh was exec'd into."""
    return dict(c.removeprefix("env ").split("=", 1) for c in calls if c.startswith("env "))


def test_entrypoint_runs_as_the_owner_of_the_first_mount_not_owned_by_root(entrypoint):
    calls = entrypoint("proxy", "--host", "0.0.0.0", STUB_U_ca="1234", STUB_G_ca="2345")
    assert calls[-1] == setpriv(1234, 2345, "proxy", "--host", "0.0.0.0")
    # the directories Docker created as root go to that user; the CA dir is already theirs
    assert chowned(calls) == dict.fromkeys(("captures", "downloads", "processed"), "1234:2345")


def test_entrypoint_falls_back_to_uid_1000_when_every_mount_is_new(entrypoint):
    calls = entrypoint("serve")
    assert calls[-1] == setpriv(1000, 1000, "serve")
    assert chowned(calls) == dict.fromkeys(DATA_DIRS.values(), "1000:1000")


def test_entrypoint_prefers_an_explicit_uid(entrypoint):
    calls = entrypoint("serve", WX_DLH_UID="4321", STUB_U_ca="1234", STUB_G_ca="2345")
    assert calls[-1] == setpriv(4321, 4321, "serve")
    assert chowned(calls) == dict.fromkeys(("captures", "downloads", "processed"), "4321:4321")


def test_entrypoint_leaves_the_user_alone_when_compose_already_set_one(entrypoint):
    calls = entrypoint("download", "--list", STUB_ID="1000")
    assert calls == ["wx-dlh download --list"]


def test_entrypoint_clears_the_proxy_variables_a_docker_client_injects(entrypoint):
    """Downloads go straight to the CDN, so an injected proxy must not survive the start."""
    injected = dict.fromkeys(PROXY_VARS, "http://docker-client-proxy:3128")
    calls = entrypoint("download", "--list", STUB_ID="1000", NO_PROXY="localhost", **injected)
    # NO_PROXY stays: with nothing routed through a proxy it changes nothing either way
    assert proxy_env(calls) == {"NO_PROXY": "localhost"}


def test_entrypoint_keeps_an_outbound_proxy_the_operator_asked_for(entrypoint):
    calls = entrypoint(
        "download",
        "--list",
        STUB_ID="1000",
        WX_DLH_OUTBOUND_PROXY="http://outbound:3128",
        HTTP_PROXY="http://docker-client-proxy:3128",
    )
    assert proxy_env(calls) == dict.fromkeys(PROXY_VARS, "http://outbound:3128")
