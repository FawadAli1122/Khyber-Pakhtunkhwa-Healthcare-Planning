import subprocess
from unittest.mock import patch, MagicMock

import pytest

from server import telegram_rebuild


@pytest.mark.parametrize("func_name,script_name", [
    ("rebuild_report", "14_build_html_report.py"),
    ("rebuild_downstream", "run_downstream.py"),
    ("rebuild_downstream_facilities", "run_downstream_facilities.py"),
])
def test_rebuild_success_returns_ok_true_no_warning(func_name, script_name):
    func = getattr(telegram_rebuild, func_name)
    completed = MagicMock(returncode=0, stderr="")
    with patch("subprocess.run", return_value=completed) as mock_run:
        ok, warning = func()
    assert ok is True
    assert warning is None
    assert script_name in str(mock_run.call_args[0][0])


@pytest.mark.parametrize("func_name", [
    "rebuild_report", "rebuild_downstream", "rebuild_downstream_facilities",
])
def test_rebuild_nonzero_returncode_returns_warning(func_name):
    func = getattr(telegram_rebuild, func_name)
    completed = MagicMock(returncode=1, stderr="boom")
    with patch("subprocess.run", return_value=completed):
        ok, warning = func()
    assert ok is False
    assert "boom" in warning


@pytest.mark.parametrize("func_name", [
    "rebuild_report", "rebuild_downstream", "rebuild_downstream_facilities",
])
def test_rebuild_timeout_returns_warning(func_name):
    func = getattr(telegram_rebuild, func_name)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
        ok, warning = func()
    assert ok is False
    assert "timed out" in warning.lower()
