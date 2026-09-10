"""poll_gpu_metrics reads NVML directly — no subprocess, no environment reads.

pynvml is imported inside the function, so these tests swap a fake module
into sys.modules rather than patching an attribute.
"""

import sys
import types

import pytest

from backend.gpu_monitor import GpuMetrics, poll_gpu_metrics

_MB = 1024 * 1024


class _FakeMemory:
    def __init__(self, used, total):
        self.used = used
        self.total = total


def _fake_pynvml(temp=62, used=2048 * _MB, total=8192 * _MB, uuids=("GPU-aaaa-bbbb",)):
    calls = {"init": 0, "shutdown": 0, "handles": []}

    class NVMLError(Exception):
        pass

    def nvmlInit():
        calls["init"] += 1

    def nvmlShutdown():
        calls["shutdown"] += 1

    def nvmlDeviceGetCount():
        return len(uuids)

    def nvmlDeviceGetHandleByIndex(index):
        calls["handles"].append(index)
        return f"handle-{index}"

    def nvmlDeviceGetUUID(handle):
        return uuids[int(str(handle).rsplit("-", 1)[1])]

    def nvmlDeviceGetTemperature(handle, sensor):
        return temp

    def nvmlDeviceGetMemoryInfo(handle):
        return _FakeMemory(used, total)

    module = types.SimpleNamespace(
        NVMLError=NVMLError,
        NVML_TEMPERATURE_GPU=0,
        nvmlInit=nvmlInit,
        nvmlShutdown=nvmlShutdown,
        nvmlDeviceGetCount=nvmlDeviceGetCount,
        nvmlDeviceGetHandleByIndex=nvmlDeviceGetHandleByIndex,
        nvmlDeviceGetUUID=nvmlDeviceGetUUID,
        nvmlDeviceGetTemperature=nvmlDeviceGetTemperature,
        nvmlDeviceGetMemoryInfo=nvmlDeviceGetMemoryInfo,
    )
    return module, calls


@pytest.fixture
def no_torch(monkeypatch):
    # Most tests exercise the index-0 fallback; the UUID match gets its own test.
    monkeypatch.setitem(sys.modules, "torch", None)


def test_vram_free_mb_computed_from_used_and_total():
    metrics = GpuMetrics(temp_c=60.0, vram_used_mb=2000.0, vram_total_mb=8000.0)
    assert metrics.vram_free_mb == 6000.0


def test_vram_free_mb_none_when_data_missing():
    metrics = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None)
    assert metrics.vram_free_mb is None


def test_reads_temperature_and_converts_memory_bytes_to_megabytes(monkeypatch, no_torch):
    module, _calls = _fake_pynvml(temp=62, used=2048 * _MB, total=8192 * _MB)
    monkeypatch.setitem(sys.modules, "pynvml", module)
    metrics = poll_gpu_metrics()
    assert metrics.temp_c == 62.0
    assert metrics.vram_used_mb == 2048.0
    assert metrics.vram_total_mb == 8192.0


def test_shuts_nvml_down_after_a_successful_poll(monkeypatch, no_torch):
    module, calls = _fake_pynvml()
    monkeypatch.setitem(sys.modules, "pynvml", module)
    poll_gpu_metrics()
    assert calls == {"init": 1, "shutdown": 1, "handles": [0]}


def test_returns_all_none_when_pynvml_is_not_installed(monkeypatch):
    # A None entry in sys.modules makes `import pynvml` raise ImportError.
    monkeypatch.setitem(sys.modules, "pynvml", None)
    metrics = poll_gpu_metrics()
    assert metrics.temp_c is None
    assert metrics.vram_free_mb is None


def test_returns_all_none_when_nvml_cannot_initialise(monkeypatch, no_torch):
    module, _calls = _fake_pynvml()

    def _boom():
        raise module.NVMLError("driver not loaded")

    module.nvmlInit = _boom
    monkeypatch.setitem(sys.modules, "pynvml", module)
    assert poll_gpu_metrics().temp_c is None


def test_returns_all_none_and_still_shuts_down_when_a_query_fails(monkeypatch, no_torch):
    module, calls = _fake_pynvml()

    def _boom(handle, sensor):
        raise module.NVMLError("GPU is lost")

    module.nvmlDeviceGetTemperature = _boom
    monkeypatch.setitem(sys.modules, "pynvml", module)
    assert poll_gpu_metrics().temp_c is None
    assert calls["shutdown"] == 1


def test_selects_the_device_matching_torchs_cuda_uuid(monkeypatch):
    module, calls = _fake_pynvml(uuids=("GPU-first", "GPU-second"))
    monkeypatch.setitem(sys.modules, "pynvml", module)

    properties = types.SimpleNamespace(uuid="SECOND")
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            current_device=lambda: 1,
            get_device_properties=lambda index: properties,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    poll_gpu_metrics()
    assert calls["handles"][-1] == 1


def test_falls_back_to_device_zero_when_torch_cannot_answer(monkeypatch):
    module, calls = _fake_pynvml(uuids=("GPU-first", "GPU-second"))
    monkeypatch.setitem(sys.modules, "pynvml", module)

    def _boom():
        raise RuntimeError("no CUDA device")

    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(current_device=_boom))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    poll_gpu_metrics()
    assert calls["handles"][-1] == 0


@pytest.mark.parametrize(
    "label, configure",
    [
        ("success", lambda module: None),
        (
            "query_failure",
            lambda module: setattr(
                module,
                "nvmlDeviceGetTemperature",
                lambda handle, sensor: (_ for _ in ()).throw(module.NVMLError("GPU is lost")),
            ),
        ),
        (
            "init_failure",
            lambda module: setattr(
                module,
                "nvmlInit",
                lambda: (_ for _ in ()).throw(module.NVMLError("driver not loaded")),
            ),
        ),
    ],
)
def test_nvml_shutdown_is_never_called_without_a_matching_init(monkeypatch, no_torch, label, configure):
    """A shutdown without a matching init decrements NVML's process-wide
    refcount that this pack never incremented. NVML is reference-counted
    across every library in the process (e.g. a co-installed ComfyUI-Crystools
    monitor also calling nvmlInit/nvmlShutdown), so an unpaired shutdown here
    would silently kill NVML for that other consumer. The brief's two shutdown
    tests only prove shutdown happens on success and on query failure; they
    don't prove it is paired, and the init-failure path is not covered by
    either of them at all.
    """
    module, calls = _fake_pynvml()
    configure(module)
    monkeypatch.setitem(sys.modules, "pynvml", module)

    poll_gpu_metrics()

    assert calls["shutdown"] <= calls["init"], (
        f"[{label}] nvmlShutdown was called {calls['shutdown']} time(s) but "
        f"nvmlInit only {calls['init']} time(s)"
    )
