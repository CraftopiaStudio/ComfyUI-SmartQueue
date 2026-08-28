"""Polls NVIDIA GPU metrics via nvidia-smi. No third-party dependencies."""

import subprocess
from dataclasses import dataclass


@dataclass
class GpuMetrics:
    temp_c: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None
    util_pct: float | None

    @property
    def vram_free_mb(self) -> float | None:
        if self.vram_used_mb is None or self.vram_total_mb is None:
            return None
        return self.vram_total_mb - self.vram_used_mb


_EMPTY = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None, util_pct=None)


def poll_gpu_metrics(timeout: float = 5.0) -> GpuMetrics:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return _EMPTY

        first_line = result.stdout.strip().splitlines()[0]
        temp_s, used_s, total_s, util_s = (p.strip() for p in first_line.split(","))
        return GpuMetrics(
            temp_c=float(temp_s),
            vram_used_mb=float(used_s),
            vram_total_mb=float(total_s),
            util_pct=float(util_s),
        )
    except Exception:
        return _EMPTY
