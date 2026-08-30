"""Polls NVIDIA GPU metrics via nvidia-smi. No third-party dependencies."""

import os
import subprocess
from dataclasses import dataclass


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


def _target_gpu_index() -> int | None:
    """First entry of CUDA_VISIBLE_DEVICES, if numeric — matches the GPU
    index PyTorch/ComfyUI actually uses. Unset or a UUID-style value (e.g.
    'GPU-<uuid>') falls back to nvidia-smi's own default (device 0), same as
    before this function existed."""
    raw = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    return int(first) if first.isdigit() else None


def poll_gpu_metrics(timeout: float = 5.0) -> GpuMetrics:
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=temperature.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
        index = _target_gpu_index()
        if index is not None:
            cmd += ["-i", str(index)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return _EMPTY

        first_line = result.stdout.strip().splitlines()[0]
        temp_s, used_s, total_s = (p.strip() for p in first_line.split(","))
        return GpuMetrics(
            temp_c=float(temp_s),
            vram_used_mb=float(used_s),
            vram_total_mb=float(total_s),
        )
    except Exception:
        return _EMPTY
