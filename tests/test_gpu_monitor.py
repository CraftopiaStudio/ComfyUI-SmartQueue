import subprocess
from unittest.mock import patch, MagicMock

from backend.gpu_monitor import GpuMetrics, poll_gpu_metrics


def test_vram_free_mb_computed_from_used_and_total():
    metrics = GpuMetrics(temp_c=60.0, vram_used_mb=2000.0, vram_total_mb=8000.0)
    assert metrics.vram_free_mb == 6000.0


def test_vram_free_mb_none_when_data_missing():
    metrics = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None)
    assert metrics.vram_free_mb is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_parses_nvidia_smi_csv_output(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="62, 2048, 8192\n",
    )
    metrics = poll_gpu_metrics()
    assert metrics.temp_c == 62.0
    assert metrics.vram_used_mb == 2048.0
    assert metrics.vram_total_mb == 8192.0


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_returns_all_none_when_nvidia_smi_missing(mock_run):
    mock_run.side_effect = FileNotFoundError("nvidia-smi not found")
    metrics = poll_gpu_metrics()
    assert metrics.temp_c is None
    assert metrics.vram_free_mb is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_returns_all_none_on_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    metrics = poll_gpu_metrics()
    assert metrics.temp_c is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_returns_all_none_on_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5.0)
    metrics = poll_gpu_metrics()
    assert metrics.temp_c is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_targets_first_cuda_visible_device(mock_run, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,2")
    mock_run.return_value = MagicMock(returncode=0, stdout="70, 4096, 24576\n")
    poll_gpu_metrics()
    args = mock_run.call_args[0][0]
    assert "-i" in args
    assert args[args.index("-i") + 1] == "1"


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_omits_device_flag_when_cuda_visible_devices_unset(mock_run, monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    mock_run.return_value = MagicMock(returncode=0, stdout="60, 2048, 8192\n")
    poll_gpu_metrics()
    args = mock_run.call_args[0][0]
    assert "-i" not in args


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_omits_device_flag_when_cuda_visible_devices_not_numeric(mock_run, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-3a2f1c4b")
    mock_run.return_value = MagicMock(returncode=0, stdout="60, 2048, 8192\n")
    poll_gpu_metrics()
    args = mock_run.call_args[0][0]
    assert "-i" not in args
