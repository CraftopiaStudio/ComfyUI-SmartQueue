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
    clear_cache_before_wait: bool = False,
    cache_fn: Callable[[], None] | None = None,
    clock_fn: Callable[[], float] | None = None,
) -> str:
    log: list[str] = []

    if unload_models_before_wait:
        # Drops the model weights, but the CUDA allocator still holds onto
        # that memory in reserve for its own next allocation — nvidia-smi
        # won't show it as freed until clear_cache_before_wait also runs.
        unload_fn()
        log.append("Unloaded all models")

    if clear_cache_before_wait:
        # gc.collect() first: a lingering Python reference to a tensor is
        # exactly what stops the CUDA allocator from reclaiming it, so this
        # needs to run before the cache-empty call, not just alongside it —
        # matches ComfyUI's own "Free model and node cache" button (main.py).
        cache_fn()
        log.append("Cleared VRAM cache")

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
            # Declared in the exact order the node should display them in —
            # the JS extension (web/smart_queue_node.js) only ever inserts
            # section-divider and button widgets between these, it never
            # reorders a real Python-backed widget. Nodes 2.0 assigns each
            # widget's default value positionally against this declaration
            # order, so splicing an actual schema widget to a new spot in
            # node.widgets desyncs that assignment and corrupts values on
            # unrelated widgets (confirmed live: moving wait_for_click and
            # notify_toast left custom_sound_path holding a stray boolean).
            inputs=[
                io.Float.Input("fixed_delay_seconds", default=30.0, min=0.0, max=3600.0, step=1.0),
                io.Boolean.Input("wait_for_temp", default=True),
                io.Float.Input("target_temp_c", default=65.0, min=30.0, max=100.0, step=1.0),
                io.Float.Input("poll_interval_seconds", default=5.0, min=1.0, max=60.0, step=1.0),
                io.Float.Input("max_wait_seconds", default=300.0, min=0.0, max=3600.0, step=10.0),
                io.Boolean.Input(
                    "notify_toast",
                    default=False,
                    display_name="notify_popup",
                    tooltip="Show a small on-screen popup message in ComfyUI when this node finishes waiting.",
                ),
                io.Boolean.Input("notify_sound", default=False),
                io.Combo.Input("notify_sound_choice", options=["Default", "Chime", "Alert", "Custom..."], default="Default"),
                # Not optional (despite only mattering when notify_sound_choice
                # is "Custom..."): io.Schema always sorts optional inputs after
                # every required one, which would silently kick this to the end
                # of the widget list regardless of declaration order.
                io.String.Input("custom_sound_path", default=""),
                # --- OPTIONS group: the occasional toggles, collapsed by
                # default in the JS. unload_models/clear_cache act *before* the
                # wait and wait_for_click *after* it, so this is a grab bag
                # chronologically — but they share the property that matters
                # for layout: all three are off by default and rarely touched,
                # unlike fixed_delay_seconds/wait_for_temp above, which are
                # what the node exists for and stay permanently visible.
                io.Boolean.Input(
                    "unload_models_before_wait",
                    default=False,
                    display_name="unload_models",
                    tooltip="Unload all models from VRAM before waiting. Doesn't free the memory by itself — pair with clear_cache for that.",
                ),
                io.Boolean.Input(
                    "clear_cache_before_wait",
                    default=False,
                    display_name="clear_cache",
                    tooltip="Actually reclaim VRAM back to the OS/driver before waiting (gc.collect() + torch's CUDA cache empty) — this is the step that makes nvidia-smi/Task Manager usage drop.",
                ),
                io.Boolean.Input("wait_for_click", default=False),
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
        import gc

        import comfy.model_management as model_management
        from server import PromptServer

        def _clear_cache():
            gc.collect()
            model_management.soft_empty_cache()

        status = run_cooldown(
            fixed_delay_seconds=kwargs["fixed_delay_seconds"],
            wait_for_temp=kwargs["wait_for_temp"],
            target_temp_c=kwargs["target_temp_c"],
            poll_interval_seconds=kwargs["poll_interval_seconds"],
            max_wait_seconds=kwargs["max_wait_seconds"],
            unload_models_before_wait=kwargs["unload_models_before_wait"],
            clear_cache_before_wait=kwargs["clear_cache_before_wait"],
            sleep_fn=__import__("time").sleep,
            metrics_fn=poll_gpu_metrics,
            unload_fn=model_management.unload_all_models,
            cache_fn=_clear_cache,
        )

        notify_sound = kwargs["notify_sound"]
        notify_toast = kwargs["notify_toast"]
        sound_choice = kwargs["notify_sound_choice"]
        custom_sound_path = kwargs.get("custom_sound_path", "")

        # Resolve the custom sound here rather than letting the browser discover
        # it's unplayable: a failed <audio> load falls back to the default tone
        # silently, which is indistinguishable from the custom sound working.
        # Downgrading to "Default" up front makes the fallback deliberate and
        # lets the node say why in its status output (§8 promised this warning).
        if notify_sound and sound_choice == "Custom...":
            from ..sound_library import resolve as resolve_custom_sound

            if resolve_custom_sound(custom_sound_path) is None:
                detail = custom_sound_path or "no file picked"
                status += f" | Custom sound unavailable ({detail}) — played the default tone instead."
                sound_choice = "Default"
                custom_sound_path = ""

        if notify_sound or notify_toast:
            PromptServer.instance.send_sync("smart_queue.cooldown_notify", {
                "notify_sound": notify_sound,
                "notify_sound_choice": sound_choice,
                "custom_sound_path": custom_sound_path,
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
