#!/usr/bin/env python3
"""Lab 4 offline checks — the gate, redirects, ceilings, bounding, neutralisation.

The per-slice suites in _parts/ are assembled into this file at S12. Until then
this holds the scaffold checks from S1, which stay afterwards: they are cheap and
they catch the two wiring mistakes that are invisible until they cost someone an
afternoon.
"""
import pathlib
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
LAB = HERE.parent
REPO = HERE.parents[2]

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "labkit" / "python"))


class TestScaffold(unittest.TestCase):
    """S1. The lab exists, imports, and cannot leak its own run artefacts."""

    def test_fixture_estate_imports(self):
        import fixture_sites
        from fixture_sites import dns, origins, sink          # noqa: F401
        self.assertIn("one seam per concern", fixture_sites.__doc__.lower())

    def test_web_tools_imports(self):
        import web_tools                                      # noqa: F401

    def test_lab_directories_exist(self):
        for rel in ("fixtures", "reference/checkpoints", "python/_parts"):
            self.assertTrue((LAB / rel).is_dir(), f"missing: {rel}")

    def test_sink_log_is_gitignored(self):
        """The sink log records whatever a half-finished gate let through.

        That is attacker traffic plus whatever the agent was carrying, and it
        must not be committable. `git check-ignore` asks git itself rather than
        grepping .gitignore, so a rule that exists but does not match still
        fails this.
        """
        target = LAB / "fixtures" / ".sink.log"
        result = subprocess.run(["git", "check-ignore", "-q", str(target)],
                                cwd=REPO)
        self.assertEqual(result.returncode, 0,
                         f"{target.relative_to(REPO)} is NOT gitignored")

    def test_live_tests_are_opt_in(self):
        """House rule: nothing costs money unless LAB_LIVE is set."""
        makefile = (REPO / "Makefile").read_text()
        self.assertIn("lab4-test", makefile)
        self.assertNotIn("LAB_LIVE=1 python3", makefile)


if __name__ == "__main__":
    unittest.main(verbosity=2)
