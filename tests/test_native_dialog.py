from unittest.mock import patch, MagicMock

from backend.native_dialog import _run_dialog


@patch("backend.native_dialog.subprocess.run")
def test_macos_uses_osascript(mock_run, monkeypatch):
    monkeypatch.setattr("backend.native_dialog.sys.platform", "darwin")
    mock_run.return_value = MagicMock(returncode=0, stdout="/Users/x/sound.wav\n")
    result = _run_dialog("Pick a sound")
    assert mock_run.call_args[0][0][0] == "osascript"
    assert result == "/Users/x/sound.wav"


@patch("backend.native_dialog.subprocess.run")
def test_macos_returns_empty_on_cancel(mock_run, monkeypatch):
    monkeypatch.setattr("backend.native_dialog.sys.platform", "darwin")
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert _run_dialog("Pick a sound") == ""


@patch("backend.native_dialog.subprocess.run")
def test_linux_uses_zenity_when_available(mock_run, monkeypatch):
    monkeypatch.setattr("backend.native_dialog.sys.platform", "linux")
    mock_run.return_value = MagicMock(returncode=0, stdout="/home/x/sound.wav\n")
    result = _run_dialog("Pick a sound")
    assert mock_run.call_args[0][0][0] == "zenity"
    assert result == "/home/x/sound.wav"


@patch("backend.native_dialog.subprocess.run")
def test_linux_falls_back_to_kdialog_when_zenity_missing(mock_run, monkeypatch):
    monkeypatch.setattr("backend.native_dialog.sys.platform", "linux")
    mock_run.side_effect = [FileNotFoundError(), MagicMock(returncode=0, stdout="/home/x/sound.wav\n")]
    result = _run_dialog("Pick a sound")
    assert mock_run.call_args_list[1][0][0][0] == "kdialog"
    assert result == "/home/x/sound.wav"


@patch("backend.native_dialog.subprocess.run")
def test_linux_returns_empty_when_no_picker_installed(mock_run, monkeypatch):
    monkeypatch.setattr("backend.native_dialog.sys.platform", "linux")
    mock_run.side_effect = FileNotFoundError()
    assert _run_dialog("Pick a sound") == ""
