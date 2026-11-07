"""The conftest guard fails a test that leaks the MHA fastpath setting."""

from __future__ import annotations

pytest_plugins = ["pytester"]


def test_guard_fails_a_leaking_test_and_restores_the_setting(pytester):
    pytester.makeconftest((__import__("pathlib").Path(__file__).parent
                           / "conftest.py").read_text())
    pytester.makepyfile("""
        import torch

        def test_leaks():
            torch.backends.mha.set_fastpath_enabled(False)

        def test_after():
            assert torch.backends.mha.get_fastpath_enabled()
    """)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*MHA fastpath changed*isolated_actor_runtime*"])
