"""Polls NVIDIA GPU metrics through NVML (the nvidia-ml-py binding).

NVML is a ctypes binding onto libnvidia-ml, so this reads the driver in
process. The previous implementation shelled out to nvidia-smi, which put a
process spawn on a path reachable from an unauthenticated POST /prompt via
the cooldown node's wait_for_temp widget - the shape the Comfy Registry bans
under policy-v0.2. Nothing here starts a process or reads the environment.
"""

from dataclasses import dataclass

_BYTES_PER_MB = 1024 * 1024


@dataclass
class GpuMetrics:
    temp_c: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None

    @property
    def vram_free_mb(self) -> float | None:
        if self.vram_used_mb is None or self.vram_total_mb is None:
            return None
        return self.vram_total_mb - self.vram_used_mb


_EMPTY = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None)


def _normalize_uuid(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip().lower().removeprefix("gpu-")


def _target_handle(pynvml):
    """Handle for the GPU ComfyUI itself renders on.

    NVML enumerates in PCI bus order while CUDA honours CUDA_VISIBLE_DEVICES,
    so NVML index 0 is not necessarily the card torch is using. Matching on
    UUID gets the right one without reading the environment - which also
    removes the `python_environment_manipulation` scanner finding the old
    CUDA_VISIBLE_DEVICES lookup attracted. Falls back to index 0 whenever
    torch, CUDA or the UUID is unavailable, which is what nvidia-smi did by
    default anyway.
    """
    try:
        import torch

        target = _normalize_uuid(
            torch.cuda.get_device_properties(torch.cuda.current_device()).uuid
        )
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            if _normalize_uuid(pynvml.nvmlDeviceGetUUID(handle)) == target:
                return handle
    except Exception:
        pass
    return pynvml.nvmlDeviceGetHandleByIndex(0)


def poll_gpu_metrics(timeout: float = 5.0) -> GpuMetrics:
    """Current temperature and VRAM for the active GPU, or all-None on any
    failure - no NVIDIA driver, no nvidia-ml-py, a lost GPU. Callers treat an
    all-None result as "temperature and VRAM rules unavailable" and carry on.

    `timeout` is accepted for call-site compatibility and unused: these are
    direct library calls, with no process to wait on.
    """
    try:
        import pynvml
    except Exception:
        return _EMPTY

    try:
        pynvml.nvmlInit()
    except Exception:
        return _EMPTY

    try:
        handle = _target_handle(pynvml)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return GpuMetrics(
            temp_c=float(temp),
            vram_used_mb=memory.used / _BYTES_PER_MB,
            vram_total_mb=memory.total / _BYTES_PER_MB,
        )
    except Exception:
        return _EMPTY
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
