import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SOUND_FILES = {
    Default: "sounds/default.wav",
    Chime: "sounds/chime.wav",
    Alert: "sounds/alert.wav",
};

const COOLDOWN_NODE_CLASS = "RubzGpuCooldownNode";
const BLINK_COLOR = "#8b6914";
const BLINK_INTERVAL_MS = 500;

// Widgets, in schema-input order, that make up the "Wait for click" and
// "Notifications" sections — used to insert separator lines between groups
// without hardcoding array indices that would drift if the schema changes.
const WAIT_SECTION_FIRST_WIDGET = "wait_for_click";
const NOTIFY_SECTION_FIRST_WIDGET = "notify_toast";

// pending wait state, keyed by node id (string) -> { promptId }
const pending = new Map();

const DIVIDER_HEIGHT = 20;

// Styled after ComfyUI-CraftKit's own section-header dividers
// (js/shared/canvas_widgets.mjs::createDividerWidget) for a consistent look
// across this user's custom node packs. #555 rather than a more subtle #333 —
// Nodes 2.0 renders this canvas at 2x internal scale and then CSS-scales it
// again with the graph zoom, which softens a thin line enough that #333
// became invisible there (classic mode draws at native resolution and stayed
// sharp) — CraftKit hit this first, so we match their fix.
function addSeparator(node, label, beforeWidgetName) {
    const widget = {
        type: "custom",
        name: `_div_${label}`,
        value: null,
        options: {},
        computeSize(width) {
            return [width, DIVIDER_HEIGHT];
        },
        draw(ctx, drawNode, widgetWidth, y, height) {
            const w = drawNode?.size?.[0] || widgetWidth;
            const margin = 14;
            const gap = 8;
            ctx.save();
            ctx.font = "bold 10px sans-serif";
            ctx.textBaseline = "middle";
            ctx.textAlign = "center";
            const cy = y + height / 2;
            const cx = w / 2;
            const textW = ctx.measureText(label).width;
            ctx.fillStyle = "#888";
            ctx.fillText(label, cx, cy);
            ctx.strokeStyle = "#555";
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(margin, cy);
            ctx.lineTo(cx - textW / 2 - gap, cy);
            ctx.moveTo(cx + textW / 2 + gap, cy);
            ctx.lineTo(w - margin, cy);
            ctx.stroke();
            ctx.restore();
        },
        serialize: false,
    };

    const targetIndex = node.widgets?.findIndex((w) => w.name === beforeWidgetName) ?? -1;
    if (targetIndex === -1) {
        node.addCustomWidget(widget);
    } else {
        node.addCustomWidget(widget);
        node.widgets.splice(node.widgets.indexOf(widget), 1);
        node.widgets.splice(targetIndex, 0, widget);
    }
    return widget;
}

// Dynamically show/hide a widget (e.g. a field that only makes sense when
// another toggle is on) in a way that works in both classic LiteGraph and
// Nodes 2.0. Ported from ComfyUI-CraftKit's own
// js/shared/widget_visibility.mjs (setWidgetVisible) so this pack doesn't
// depend on CraftKit being installed.
function setWidgetVisible(node, widget, visible) {
    if (!widget._visibilityDefaultCaptured) {
        widget._defaultComputeSize = widget.computeSize;
        widget._visibilityDefaultCaptured = true;
    }
    widget.computeSize = visible ? widget._defaultComputeSize : () => [0, -4];
    widget.hidden = !visible;
    if (!widget.options) widget.options = {};
    widget.options.hidden = !visible;

    // Nodes 2.0's widget list is a Vue shallowReactive array that only
    // tracks the array's own indices/length, not properties nested inside
    // each widget — a push-then-splice-it-back no-op forces a real
    // structural mutation so Vue re-evaluates and picks up the new state.
    const nudge = { name: "__visibility_nudge__", type: "custom", value: null, computeSize: () => [0, 0], draw() {} };
    node.widgets.push(nudge);
    node.widgets.splice(node.widgets.indexOf(nudge), 1);

    node.setDirtyCanvas(true, true);
}

function setNodeWaiting(node, waiting) {
    if (waiting) {
        let on = true;
        node.bgcolor = BLINK_COLOR;
        node._smartQueueBlinkId = setInterval(() => {
            on = !on;
            node.bgcolor = on ? BLINK_COLOR : undefined;
            app.graph.setDirtyCanvas(true, false);
        }, BLINK_INTERVAL_MS);
    } else {
        clearInterval(node._smartQueueBlinkId);
        node._smartQueueBlinkId = null;
        node.bgcolor = undefined;
    }

    const { continueBtn, cancelBtn } = node._smartQueueWaitWidgets ?? {};
    if (continueBtn) continueBtn.disabled = !waiting;
    if (cancelBtn) cancelBtn.disabled = !waiting;
    app.graph.setDirtyCanvas(true, false);
}

app.registerExtension({
    name: "SmartQueue.CooldownNode",
    settings: [
        {
            id: "SmartQueue.AlwaysToastOnWait",
            name: "Always show a toast when a Cooldown node is waiting (even if its own \"notify toast\" is off)",
            type: "boolean",
            defaultValue: false,
            tooltip: "Handy if you tend to lose track of the node in larger workflows.",
            category: ["SmartQueue", "6. Cooldown Node", "Always show a toast when a Cooldown node is waiting"],
        },
    ],
    nodeCreated(node) {
        if (node.comfyClass !== COOLDOWN_NODE_CLASS) return;

        // wait_for_click, notify_toast, and custom_sound_path are declared in
        // backend/nodes/cooldown.py in exactly the order they should appear
        // here — do NOT reorder any of them (or any other real Python-backed
        // widget) via node.widgets.splice(). Nodes 2.0 assigns each widget's
        // default value positionally against the backend declaration order,
        // so moving a schema widget in this array desyncs that assignment
        // and corrupts values on unrelated widgets (confirmed live: this
        // used to leave custom_sound_path holding a stray boolean `true`).
        // Only divider and button widgets (JS-only, not schema-backed) are
        // safe to splice in below.
        addSeparator(node, "NOTIFICATIONS", NOTIFY_SECTION_FIRST_WIDGET);
        addSeparator(node, "OPTIONS", WAIT_SECTION_FIRST_WIDGET);

        // Only show the temp-wait tuning fields while wait_for_temp is on —
        // they're meaningless (and the whole point of confusion) otherwise.
        const waitForTempWidget = node.widgets.find((w) => w.name === "wait_for_temp");
        const tempSubWidgets = ["target_temp_c", "poll_interval_seconds", "max_wait_seconds"]
            .map((name) => node.widgets.find((w) => w.name === name))
            .filter(Boolean);
        if (waitForTempWidget && tempSubWidgets.length) {
            const updateTempVisibility = () => {
                for (const w of tempSubWidgets) setWidgetVisible(node, w, waitForTempWidget.value);
            };
            const origCallback = waitForTempWidget.callback;
            waitForTempWidget.callback = function (...args) {
                origCallback?.call(this, ...args);
                updateTempVisibility();
            };
            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (...args) {
                origOnConfigure?.call(this, ...args);
                updateTempVisibility();
            };
            updateTempVisibility();
        }

        // Only show the sound-choice fields while notify_sound is on, and
        // only show the custom path field once "Custom..." is picked.
        const notifySoundWidget = node.widgets.find((w) => w.name === "notify_sound");
        const notifySoundChoiceWidget = node.widgets.find((w) => w.name === "notify_sound_choice");
        const customSoundPathWidget = node.widgets.find((w) => w.name === "custom_sound_path");
        let browseBtn = null;
        if (customSoundPathWidget && notifySoundChoiceWidget) {
            // Browse button, copied from CraftKit's Smart Batch Resize
            // (js/smart_batch_resize.js) "📁 Browse folder" pattern.
            browseBtn = node.addWidget("button", "📁 Browse sound file", null, async () => {
                try {
                    const res = await fetch("/smart_queue/browse_sound_file", { method: "POST" });
                    const data = await res.json();
                    if (data.ok && data.path) {
                        customSoundPathWidget.value = data.path;
                        node.setDirtyCanvas(true);
                    }
                } catch (e) {
                    console.error("[Smart Queue] Browse failed:", e);
                }
            }, { serialize: false });
            browseBtn.serialize = false;

            // Move Browse button to right after custom_sound_path
            const pathIdx = node.widgets.indexOf(customSoundPathWidget);
            node.widgets.splice(node.widgets.indexOf(browseBtn), 1);
            node.widgets.splice(pathIdx + 1, 0, browseBtn);
        }
        if (notifySoundWidget && notifySoundChoiceWidget && customSoundPathWidget) {
            const updateSoundVisibility = () => {
                const soundOn = notifySoundWidget.value;
                const showCustomPath = soundOn && notifySoundChoiceWidget.value === "Custom...";
                setWidgetVisible(node, notifySoundChoiceWidget, soundOn);
                setWidgetVisible(node, customSoundPathWidget, showCustomPath);
                if (browseBtn) setWidgetVisible(node, browseBtn, showCustomPath);
            };
            const origSoundCallback = notifySoundWidget.callback;
            notifySoundWidget.callback = function (...args) {
                origSoundCallback?.call(this, ...args);
                updateSoundVisibility();
            };
            const origChoiceCallback = notifySoundChoiceWidget.callback;
            notifySoundChoiceWidget.callback = function (...args) {
                origChoiceCallback?.call(this, ...args);
                updateSoundVisibility();
            };
            const origOnConfigureSound = node.onConfigure;
            node.onConfigure = function (...args) {
                origOnConfigureSound?.call(this, ...args);
                updateSoundVisibility();
            };
            updateSoundVisibility();
        }

        // Plain default litegraph button — no custom draw/computeSize, copied
        // straight from CraftKit's own "▶ Run Batch" (js/smart_batch_resize.js),
        // which is itself just an unstyled node.addWidget("button", ...). The
        // user tried a taller, colored-chip variant first and asked to go back
        // to this exact plain look instead.
        const continueBtn = node.addWidget("button", "▶ Continue", "continue", () => {
            const state = pending.get(String(node.id));
            if (!state) return;
            pending.delete(String(node.id));
            setNodeWaiting(node, false);
            fetch(`/smart_queue/continue/${encodeURIComponent(state.promptId)}`, { method: "POST" }).catch((err) => {
                console.error("[Smart Queue] continue request failed:", err);
            });
        }, { serialize: false });
        continueBtn.serialize = false;
        continueBtn.disabled = true;

        const cancelBtn = node.addWidget("button", "✕ Cancel", "cancel", () => {
            const state = pending.get(String(node.id));
            if (!state) return;
            pending.delete(String(node.id));
            setNodeWaiting(node, false);
            fetch(`/smart_queue/cancel_wait/${encodeURIComponent(state.promptId)}`, { method: "POST" }).catch((err) => {
                console.error("[Smart Queue] cancel request failed:", err);
            });
        }, { serialize: false });
        cancelBtn.serialize = false;
        cancelBtn.disabled = true;

        node._smartQueueWaitWidgets = { continueBtn, cancelBtn };

        // Force a full size/layout recompute now that some widgets may have
        // started out hidden (wait_for_temp/notify_sound default to off).
        node.setSize(node.computeSize());
        node.setDirtyCanvas(true, true);
    },
    async setup() {
        api.addEventListener("smart_queue.cooldown_notify", (event) => {
            const { notify_sound, notify_sound_choice, custom_sound_path, notify_toast, status } = event.detail;

            if (notify_sound) {
                let src = SOUND_FILES[notify_sound_choice] ?? SOUND_FILES.Default;
                if (notify_sound_choice === "Custom..." && custom_sound_path) {
                    src = custom_sound_path;
                }
                const audio = new Audio(new URL(src, import.meta.url).href);
                audio.play().catch(() => {
                    // Custom file missing/unplayable — fall back to the default tone.
                    new Audio(new URL(SOUND_FILES.Default, import.meta.url).href).play().catch(() => {});
                });
            }

            if (notify_toast && app.extensionManager?.toast) {
                app.extensionManager.toast.add({ severity: "info", summary: "Smart Cooldown & Pause", detail: status });
            }
        });

        api.addEventListener("smart_queue.cooldown_wait_for_click", (event) => {
            const { prompt_id, node_id } = event.detail;
            const node = node_id != null ? app.graph.getNodeById(node_id) : null;

            if (node?._smartQueueWaitWidgets) {
                pending.set(String(node.id), { promptId: prompt_id });
                setNodeWaiting(node, true);
            }

            const alwaysToast = app.extensionManager?.setting?.get("SmartQueue.AlwaysToastOnWait");
            if ((alwaysToast || !node) && app.extensionManager?.toast) {
                app.extensionManager.toast.add({
                    severity: "info",
                    summary: "Smart Cooldown & Pause",
                    detail: "Waiting for Continue — click it on the node to resume.",
                });
            }
        });
    },
});
