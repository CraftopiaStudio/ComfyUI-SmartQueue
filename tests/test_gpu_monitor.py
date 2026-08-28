import subprocess
from unittest.mock import patch, MagicMock

from backend.gpu_monitor import GpuMetrics, poll_gpu_metrics


def test_vram_free_mb_computed_from_used_and_total():
    metrics = GpuMetrics(temp_c=60.0, vram_used_mb=2000.0, vram_total_mb=8000.0, util_pct=10.0)
    assert metrics.vram_free_mb == 6000.0


def test_vram_free_mb_none_when_data_missing():
    metrics = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None, util_pct=None)
    assert metrics.vram_free_mb is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_parses_nvidia_smi_csv_output(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="62, 2048, 8192, 15\n",
    )
    metrics = poll_gpu_metrics()
    assert metrics.temp_c == 62.0
    assert metrics.vram_used_mb == 2048.0
    assert metrics.vram_total_mb == 8192.0
    assert metrics.util_pct == 15.0


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
