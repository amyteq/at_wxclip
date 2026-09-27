"""Draft Claude PreToolUse guard. Installing the draft is a separate owner step.

Recognises literal rm and Git cleanup commands; this is not a shell security
boundary. Other programs, scripts, APIs and command spellings still need the
sandbox, classifier and repository policy. Existing guard_tool.py is retained
and consulted except for the narrowly proven cleanup exceptions below.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

LEGACY_GUARD_ENABLED = False


def decision(kind: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": kind,
            "permissionDecisionReason": reason,
        }
    }


def disposable(branch: str) -> bool:
    branch = branch.removeprefix("refs/heads/")
    return (
        bool(re.fullmatch(r"(?:doc/|docs/|temp/|temp-)[A-Za-z0-9_./-]+", branch))
        and not any(part in {"", ".", ".."} for part in branch.split("/"))
        and ".." not in branch
    )


def protected(branch: str) -> bool:
    branch = branch.removeprefix("refs/heads/")
    return branch.startswith("dev/") or branch in {
        "main",
        "master",
        "production",
        "release",
        "gh-pages",
    }


def tmp_operand(operand: str) -> bool:
    """Allow descendants only, with globs only in the last path component."""
    if not operand.startswith("/tmp/") or any(x in operand for x in ("$", "`", "\\", "{", "}")):
        return False
    path = Path(operand)
    if ".." in path.parts or path == Path("/tmp"):
        return False
    if any(x in str(path.parent) for x in ("*", "?", "[")):
        return False
    root = Path("/tmp").resolve()
    try:
        if not path.parent.resolve().is_relative_to(root):
            return False
        # Do not use a symlink as a directory, even when it points into /tmp.
        if any(p.is_symlink() for p in (path.parent, *path.parent.parents)):
            return False
        if operand.endswith("/") and path.is_symlink():
            return False
        # Retain the existing frozen-source guard, including expanded globs.
        matches = glob.glob(operand)
        if any("source-of-truth" in Path(p).parts for p in [operand, *matches]):
            return False
    except OSError:
        return False
    return True


def inspect(command: str, project: Path) -> tuple[str | None, str, bool]:
    """Return decision, reason, and whether the legacy guard must be consulted."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return "ask", "Cannot parse the command; review its effects.", False
    if not tokens:
        return None, "", True
    risky = any(Path(t).name == "rm" for t in tokens) or (
        any(Path(t).name == "git" for t in tokens)
        and any(
            t in {"-d", "-D", "--delete", "--hard", "-f", "--force"}
            or t.startswith(("--force-with-lease", ":"))
            for t in tokens
        )
    )
    complex_shell = any(c in command for c in ("\n", "`", "$", "\\")) or any(
        t and set(t) <= set(";&|()<>") for t in tokens
    )
    if risky and complex_shell:
        return (
            "ask",
            "Split destructive shell compositions into literal commands for review.",
            False,
        )
    program, args = Path(tokens[0]).name, tokens[1:]
    if risky and tokens[0] not in {
        "rm",
        "/bin/rm",
        "/usr/bin/rm",
        "git",
        "/bin/git",
        "/usr/bin/git",
    }:
        return "ask", "A wrapped or unrecognised executable needs review.", False
    if program == "rm":
        operands, after_options = [], False
        for arg in args:
            if not after_options and arg == "--":
                after_options = True
            elif not after_options and arg.startswith("-"):
                if not re.fullmatch(r"-[firdIvR]+", arg) and arg not in {
                    "--force",
                    "--recursive",
                    "--verbose",
                    "--interactive",
                    "--one-file-system",
                    "--preserve-root",
                }:
                    return (
                        "ask",
                        "Unrecognised rm option; review the complete command.",
                        False,
                    )
            else:
                after_options = True
                operands.append(arg)
        if operands and all(tmp_operand(p) for p in operands):
            return (
                "allow",
                "Owner-approved literal deletion confined to /tmp descendants.",
                False,
            )
        return (
            "ask",
            "Deletion is outside the literal /tmp exception or its target is uncertain.",
            False,
        )
    if program != "git":
        if risky:
            return "ask", "A wrapped destructive command needs review.", False
        return None, "", True
    git_cwd = project
    while args and args[0] == "-C":
        if len(args) < 3:
            return "ask", "Incomplete Git directory option.", False
        git_cwd = (git_cwd / args[1]).resolve()
        args = args[2:]
    if not args:
        return None, "", True
    if risky and (git_cwd != project.resolve() or args[0].startswith("-")):
        return (
            "ask",
            "Git cleanup must name this checkout without configuration overrides.",
            False,
        )
    verb, rest = args[0], args[1:]
    branches = None
    if verb == "branch" and any(x in {"-d", "-D", "--delete"} for x in rest):
        if any(x.startswith("-") and x not in {"-d", "-D", "--delete", "--"} for x in rest):
            return "ask", "Review nonstandard branch deletion flags.", False
        branches = [x for x in rest if not x.startswith("-")]
    elif verb == "update-ref" and "-d" in rest:
        if len(rest) != 2 or rest[0] != "-d" or not rest[1].startswith("refs/heads/"):
            return "ask", "Review nonstandard Git reference deletion.", False
        branches = [rest[1]]
    elif verb == "push":
        deletion = any(x in {"--delete", "-d"} or x.startswith(":") for x in rest)
        force = any(
            x in {"--force", "-f"} or x.startswith(("--force=", "--force-with-lease", "+"))
            for x in rest
        )
        if deletion:
            if any(x.startswith("-") and x not in {"--delete", "-d"} for x in rest):
                return "ask", "Review combined remote deletion flags.", False
            positional = [x for x in rest if not x.startswith("-")]
            if not positional or positional[0] != "origin":
                return (
                    "ask",
                    "Disposable remote deletion is restricted to origin.",
                    False,
                )
            targets = positional[1:]
            if not any(x in {"--delete", "-d"} for x in rest) and not all(
                x.startswith(":") for x in targets
            ):
                return "ask", "Do not combine remote deletion with a push.", False
            branches = [x.removeprefix(":") for x in targets]
        elif force:
            lease = [x for x in rest if x.startswith("--force-with-lease=")]
            positional = [x for x in rest if not x.startswith("-")]
            if (
                len(lease) == 1
                and len(rest) == 3
                and len(positional) == 2
                and positional[0] == "origin"
            ):
                match = re.fullmatch(
                    r"--force-with-lease=(refs/heads/[^:]+):([0-9a-f]{40})", lease[0]
                )
                if match and disposable(match[1]) and positional[1] == "HEAD:" + match[1]:
                    return (
                        "allow",
                        "Disposable branch push uses an explicit expected-head lease.",
                        False,
                    )
            return (
                "deny",
                "Force push needs one disposable destination and an exact expected-head lease.",
                False,
            )
    elif verb == "reset" and "--hard" in rest:
        return (
            "ask",
            "Hard reset can discard work independently of the branch name.",
            False,
        )
    if branches is not None:
        if any(protected(x) for x in branches):
            return (
                "deny",
                "Maintained branches and every dev/ branch are protected from deletion.",
                False,
            )
        if branches and all(disposable(x) for x in branches):
            return (
                "allow",
                "Owner-approved deletion of doc/, docs/, temp/ or temp- branches.",
                False,
            )
        return (
            "ask",
            "Branch deletion is outside the exact disposable-prefix exception.",
            False,
        )
    if risky:
        return "ask", "Review this destructive Git spelling.", False
    return None, "", True


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("Expected an object")
        root = Path(
            os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
        ).resolve()
        tool_input = data.get("tool_input", {})
        if not isinstance(tool_input, dict):
            raise TypeError("Expected tool_input object")
        if data.get("tool_name") == "Bash":
            command = tool_input.get("command")
            if not isinstance(command, str):
                raise TypeError("Expected command string")
            kind, reason, delegate = inspect(command, root)
            actual_cwd = Path(data.get("cwd") or os.getcwd()).resolve()
            if (
                kind == "allow"
                and Path(shlex.split(command)[0]).name == "git"
                and actual_cwd != root
            ):
                kind, reason = (
                    "ask",
                    "Git cleanup must run from the approved project root.",
                )
            if kind:
                print(json.dumps(decision(kind, reason)))
                return 0
            if not delegate:
                return 0
        legacy = root / ".claude/hooks/guard_tool.py"
        if (
            LEGACY_GUARD_ENABLED
            and legacy.exists()
            and legacy.resolve() != Path(__file__).resolve()
        ):
            result = subprocess.run(
                [sys.executable, str(legacy)],
                input=raw,
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
                cwd=root,
            )
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode not in {0, 2}:
                raise RuntimeError("Existing hook did not finish normally")
            return result.returncode
        return 0
    except Exception:  # noqa: BLE001 - an unexpected hook failure must deny
        print(
            json.dumps(
                decision(
                    "deny",
                    "Permission guard failed; review or repair it before retrying.",
                )
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
