"""Regression tests simulate tool requests; no deletion or Git mutation runs."""

import fnmatch
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "draft_guard", HERE / "guard_development_permissions.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def test_settings_do_not_override_cleanup_exceptions(self):
        if not (HERE / "settings.project.json").exists():
            self.skipTest("Run the distributed package to check settings integration")
        commands = [
            "rm /tmp/*",
            "rm -rf /tmp/*",
            "git branch -D docs/example",
            "git push origin --delete temp/example",
            "git push --delete origin temp/example",
            "git push origin :temp/example",
            "git update-ref -d refs/heads/temp/example",
            (
                "git push origin HEAD:refs/heads/temp/example "
                "--force-with-lease=refs/heads/temp/example:"
            )
            + "a" * 40,
        ]
        for filename in ["settings.project.json", "settings.local.proposed.json"]:
            permissions = json.loads((HERE / filename).read_text()).get("permissions", {})
            for kind in ["ask", "deny"]:
                for rule in permissions.get(kind, []):
                    if rule.startswith("Bash(") and rule.endswith(")"):
                        pattern = rule[5:-1].replace(":*", " *")
                        for command in commands:
                            with self.subTest(file=filename, rule=rule, command=command):
                                self.assertFalse(fnmatch.fnmatchcase(command, pattern))

    def test_git_cleanup_in_another_working_directory_asks(self):
        with tempfile.TemporaryDirectory(prefix="perms-821-") as directory:
            result = subprocess.run(
                [sys.executable, str(HERE / "guard_development_permissions.py")],
                input=json.dumps(
                    {
                        "tool_name": "Bash",
                        "cwd": directory,
                        "tool_input": {"command": "git branch -D temp/example"},
                    }
                ),
                env=dict(os.environ, CLAUDE_PROJECT_DIR=str(HERE)),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "ask"
            )

    def test_command_boundaries(self):
        sha = "a" * 40
        cases = [
            ("rm /tmp/*", "allow"),
            ("rm -rf /tmp/*", "allow"),
            ("rm -fr -- /tmp/build-one /tmp/build-two", "allow"),
            ("/usr/bin/rm -rf /tmp/build-one", "allow"),
            ("rm -rf *", "ask"),
            ("rm -rf .", "ask"),
            ("rm -rf /", "ask"),
            ("rm -rf /tmp", "ask"),
            ("rm -rf /tmp/", "ask"),
            ("rm -rf /tmp/../etc", "ask"),
            ("rm -rf /tmp/* /home/user/data", "ask"),
            ("rm -rf /tmp/a/../../etc", "ask"),
            ("rm -rf /tmp/*/*", "ask"),
            ("rm -rf $TMPDIR/*", "ask"),
            ("rm -rf /tmp/$(pwd)", "ask"),
            ("rm -rf /tmp/a; rm -rf *", "ask"),
            ("cd /tmp && rm -rf *", "ask"),
            ("env rm -rf /tmp/*", "ask"),
            ("/tmp/custom/rm -rf /tmp/*", "ask"),
            ("rm --no-preserve-root -rf /tmp/*", "ask"),
            ("rm -rf /tmp/source-of-truth", "ask"),
            ("rm -rf 'unterminated", "ask"),
            ("git branch -D doc/test", "allow"),
            ("git branch -d docs/test temp/test temp-one", "allow"),
            ("git branch -D dev/harry", "deny"),
            ("git branch -D dev/harry2", "deny"),
            ("git branch -D main", "deny"),
            ("git branch -D temp/test dev/harry", "deny"),
            ("git branch -D feature/test", "ask"),
            ("git branch -D temp/../dev/harry", "ask"),
            ("git branch -D temp/*", "ask"),
            ("git -C /another/project branch -D temp/test", "ask"),
            ("git -c alias.branch=evil branch -D temp/test", "ask"),
            ("git push origin --delete doc/test temp/test", "allow"),
            ("git push --delete origin temp/test", "allow"),
            ("git push origin :refs/heads/temp/test", "allow"),
            ("git push origin :temp/test main", "ask"),
            ("git push elsewhere --delete temp/test", "ask"),
            ("git push origin --delete dev/harry", "deny"),
            ("git update-ref -d refs/heads/temp/test", "allow"),
            ("git update-ref -d refs/heads/dev/test", "deny"),
            (
                "git push origin HEAD:refs/heads/temp/test --force-with-lease=refs/heads/temp/test:"
                + sha,
                "allow",
            ),
            (
                "git push origin HEAD:refs/heads/dev/test --force-with-lease=refs/heads/dev/test:"
                + sha,
                "deny",
            ),
            ("git push --force origin temp/test", "deny"),
            ("git push origin temp/test --force-with-lease", "deny"),
            ("git reset --hard HEAD", "ask"),
            ("rg 'git branch -D' .claude/settings.json", None),
            ("cat /tmp/codex-stderr.log", None),
            ("gh pr comment 12 --body-file /tmp/comment.md", None),
            ("git status --short", None),
            ("python3 -m pytest", None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(guard.inspect(command, HERE)[0], expected)

    def test_symlink_ancestor_is_not_tmp_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="perms-821-") as directory:
            link = Path(directory) / "link"
            link.symlink_to("/etc", target_is_directory=True)
            self.assertFalse(guard.tmp_operand(str(link / "*")))
            self.assertEqual(guard.inspect("rm -rf " + str(link / "*"), HERE)[0], "ask")

    def test_legacy_guard_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="perms-821-") as directory:
            root = Path(directory)
            legacy = root / ".claude/hooks/guard_tool.py"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("import sys\nsys.stderr.write('legacy protection')\nsys.exit(2)\n")
            env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
            for tool, tool_input in [
                ("Read", {"file_path": "secret"}),
                ("Bash", {"command": "npm test"}),
            ]:
                result = subprocess.run(
                    [sys.executable, str(HERE / "guard_development_permissions.py")],
                    input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                    text=True,
                    capture_output=True,
                    check=False,
                    env=env,
                )
                self.assertEqual(result.returncode, 2 if guard.LEGACY_GUARD_ENABLED else 0)
                self.assertEqual("legacy protection" in result.stderr, guard.LEGACY_GUARD_ENABLED)
            result = subprocess.run(
                [sys.executable, str(HERE / "guard_development_permissions.py")],
                input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/*"}}),
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(
                json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )

    def test_invalid_input_fails_closed(self):
        for raw in ["invalid json", "[]", '{"tool_name":"Bash","tool_input":[]}']:
            result = subprocess.run(
                [sys.executable, str(HERE / "guard_development_permissions.py")],
                input=raw,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )


if __name__ == "__main__":
    unittest.main()
