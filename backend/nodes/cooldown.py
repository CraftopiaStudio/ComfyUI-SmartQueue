"""Smart Cooldown & Pause node (V3 schema). Node type ID stays RubzGpuCooldownNode
for backward compatibility with workflows saved against the old rubz-gpu-cooldown pack.
"""

from typing import Callable

try:
    from comfy_api.latest import io  # type: ignore[import-not-found]
    _HAS_COMFY_IO = True
except ImportError:
    io = None  # type: ignore[assignment]
    _HAS_COMFY_IO = False

from ..continue_registry import wait_for_continue
from ..gpu_monitor import GpuMetrics, poll_gpu_metrics


def run_cooldown(
    fixed_delay_seconds: float,
    wait_for_temp: bool,
    target_temp_c: float,
    poll_interval_seconds: float,
    max_wait_seconds: float,
    unload_models_before_wait: bool,
    sleep_fn: Callable[[float], None],
    metrics_fn: Callable[[], GpuMetrics],
    unload_fn: Callable[[], None],
    clock_fn: Callable[[], float] | None = None,
) -> str:
    log: list[str] = []

    if unload_models_before_wait:
        unload_fn()
        log.append("Unloaded all models")

    if fixed_delay_seconds > 0:
        sleep_fn(fixed_delay_seconds)
        log.append(f"Fixed delay: {fixed_delay_seconds:.0f}s")

    if wait_for_temp:
        metrics = metrics_fn()
        if metrics.temp_c is None:
            log.append("GPU temp unavailable (nvidia-smi not found or failed) — skipping temp wait.")
        else:
            elapsed = 0.0
            log.append(f"Start temp: {metrics.temp_c:.0f}C, target: {target_temp_c:.0f}C")
            while metrics.temp_c is not None and metrics.temp_c > target_temp_c:
                if elapsed >= max_wait_seconds:
                    log.append(f"Max wait ({max_wait_seconds:.0f}s) reached at {metrics.temp_c:.0f}C — continuing anyway.")
                    break
                sleep_fn(poll_interval_seconds)
                elapsed += poll_interval_seconds
                metrics = metrics_fn()
                if metrics.temp_c is not None:
                    log.append(f"  -> {metrics.temp_c:.0f}C")
            else:
                if metrics.temp_c is not None:
                    log.append(f"Reached target: {metrics.temp_c:.0f}C")

    return " | ".join(log) if log else "No wait configured."


# Node class must stay importable even when comfy_api isn't installed (e.g. under
# pytest, outside a real ComfyUI process) so `from .backend.nodes.cooldown import
# SmartCooldownNode` in __init__.py never breaks the module-load smoke test.
_NodeBase = io.ComfyNode if _HAS_COMFY_IO else object


class SmartCooldownNode(_NodeBase):
    @classmethod
    def define_schema(cls):
        if not _HAS_COMFY_IO:
            raise RuntimeError("comfy_api is not available in this environment")
        return io.Schema(
            node_id="RubzGpuCooldownNode",
            display_name="Smart Cooldown & Pause",
            category="utils",
            inputs=[
                io.Float.Input("fixed_delay_seconds", default=30.0, min=0.0, max=3600.0, step=1.0),
                io.Boolean.Input("wait_for_temp", default=True),
                io.Float.Input("target_temp_c", default=65.0, min=30.0, max=100.0, step=1.0),
                io.Float.Input("poll_interval_seconds", default=5.0, min=1.0, max=60.0, step=1.0),
                io.Float.Input("max_wait_seconds", default=300.0, min=0.0, max=3600.0, step=10.0),
                io.Boolean.Input("unload_models_before_wait", default=False),
                io.Boolean.Input("wait_for_click", default=False),
                io.Boolean.Input("notify_sound", default=False),
                io.Combo.Input("notify_sound_choice", options=["Default", "Chime", "Alert", "Custom..."], default="Default"),
                io.String.Input("custom_sound_path", default="", optional=True),
                io.Boolean.Input("notify_toast", default=False),
                io.AnyType.Input("passthrough", optional=True),
            ],
            outputs=[
                io.AnyType.Output("passthrough"),
                io.String.Output("status"),
            ],
            hidden=[io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, **kwargs):
        import comfy.model_management as model_management
        from server import PromptServer

        status = run_cooldown(
            fixed_delay_seconds=kwargs["fixed_delay_seconds"],
            wait_for_temp=kwargs["wait_for_temp"],
            target_temp_c=kwargs["target_temp_c"],
            poll_interval_seconds=kwargs["poll_interval_seconds"],
            max_wait_seconds=kwargs["max_wait_seconds"],
            unload_models_before_wait=kwargs["unload_models_before_wait"],
            sleep_fn=__import__("time").sleep,
            metrics_fn=poll_gpu_metrics,
            unload_fn=model_management.unload_all_models,
        )

        notify_sound = kwargs["notify_sound"]
        notify_toast = kwargs["notify_toast"]
        if notify_sound or notify_toast:
            PromptServer.instance.send_sync("smart_queue.cooldown_notify", {
                "notify_sound": notify_sound,
                "notify_sound_choice": kwargs["notify_sound_choice"],
                "custom_sound_path": kwargs.get("custom_sound_path", ""),
                "notify_toast": notify_toast,
                "status": status,
            })

        if kwargs["wait_for_click"]:
            prompt_id = PromptServer.instance.last_prompt_id
            PromptServer.instance.send_sync("smart_queue.cooldown_wait_for_click", {
                "prompt_id": prompt_id,
                "node_id": cls.hidden.unique_id,
            })
            wait_for_continue(prompt_id)
            status += " | Continued by user click."

        return io.NodeOutput(kwargs.get("passthrough"), status)
