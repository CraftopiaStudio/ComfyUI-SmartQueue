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
const NOTIFY_SECTION_FIRST_WIDGET = "notify_sound";

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

        // wait_for_click only matters together with the Continue/Cancel buttons
        // below it — move it to sit directly above them instead of being
        // separated by the unrelated Notifications section (matches CraftKit's
        // own pattern of an options-style section immediately before the
        // action button, e.g. Smart Batch Resize's "OPTIONS" -> "Run Batch").
        const waitForClickWidget = node.widgets.find((w) => w.name === WAIT_SECTION_FIRST_WIDGET);
        if (waitForClickWidget) {
            node.widgets.splice(node.widgets.indexOf(waitForClickWidget), 1);
            node.widgets.push(waitForClickWidget);
        }

        addSeparator(node, "NOTIFICATIONS", NOTIFY_SECTION_FIRST_WIDGET);
        addSeparator(node, "OPTIONS", WAIT_SECTION_FIRST_WIDGET);

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
