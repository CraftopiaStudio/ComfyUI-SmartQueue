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

// Widgets, in schema-input order, that each collapsible section starts at —
// used to insert the section header above the right widget without hardcoding
// array indices that would drift if the schema changes.
// OPTIONS starts at unload_models rather than wait_for_click: the VRAM
// toggles are occasional switches too, not part of what the node is for, so
// they belong in the drawer alongside it. cooldown.py declares them
// contiguously for exactly this reason.
const WAIT_SECTION_FIRST_WIDGET = "unload_models_before_wait";
const NOTIFY_SECTION_FIRST_WIDGET = "notify_toast";

const NOTIFY_SECTION = "NOTIFICATIONS";
const WAIT_SECTION = "OPTIONS";

// pending wait state, keyed by node id (string) -> { promptId }
const pending = new Map();

const DIVIDER_HEIGHT = 20;

// Collapse state lives in node.properties, NOT in a widget. Widget values are
// serialized and restored positionally over node.widgets (see §23 in the
// design spec), so storing this as a widget would shift the indices of the
// real schema-backed widgets and corrupt their values. properties is a plain
// dict keyed by name, serialized with the node, and index-independent.
function collapseKey(label) {
    return `_sq_collapsed_${label}`;
}

function isCollapsed(node, label) {
    const stored = node.properties?.[collapseKey(label)];
    // Default collapsed: the whole point of the sections is that a fresh node
    // is short.
    return stored === undefined ? true : !!stored;
}

function setCollapsed(node, label, collapsed) {
    if (!node.properties) node.properties = {};
    node.properties[collapseKey(label)] = collapsed;
}

// Styled after ComfyUI-CraftKit's own section-header dividers
// (js/shared/canvas_widgets.mjs::createDividerWidget) for a consistent look
// across this user's custom node packs. #555 rather than a more subtle #333 —
// Nodes 2.0 renders this canvas at 2x internal scale and then CSS-scales it
// again with the graph zoom, which softens a thin line enough that #333
// became invisible there (classic mode draws at native resolution and stayed
// sharp) — CraftKit hit this first, so we match their fix.
//
// CraftKit's own divider is explicitly draw-only; the click handling here
// follows their one clickable canvas widget instead
// (js/shared/preset_picker_widget.mjs), including its triggerDraw() workaround.
function addSeparator(node, label, beforeWidgetName, onToggle) {
    const collapsible = typeof onToggle === "function";
    const widget = {
        type: "custom",
        name: `_div_${label}`,
        value: null,
        options: { serialize: false },
        // Node-local y range of the row as last drawn, for the click hit test.
        _hitY: [0, 0],
        computeSize(width) {
            return [width, DIVIDER_HEIGHT];
        },
        draw(ctx, drawNode, widgetWidth, y, height) {
            const w = drawNode?.size?.[0] || widgetWidth;
            const margin = 14;
            const gap = 8;
            const text = collapsible
                ? `${isCollapsed(node, label) ? "▸" : "▾"} ${label}`
                : label;
            ctx.save();
            ctx.font = "bold 10px sans-serif";
            ctx.textBaseline = "middle";
            ctx.textAlign = "center";
            const cy = y + height / 2;
            const cx = w / 2;
            const textW = ctx.measureText(text).width;
            ctx.fillStyle = "#888";
            ctx.fillText(text, cx, cy);
            ctx.strokeStyle = "#555";
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(margin, cy);
            ctx.lineTo(cx - textW / 2 - gap, cy);
            ctx.moveTo(cx + textW / 2 + gap, cy);
            ctx.lineTo(w - margin, cy);
            ctx.stroke();
            ctx.restore();
            this._hitY = [y, y + height];
        },
        mouse(event, pos, mouseNode) {
            if (!collapsible) return false;
            if (event.type !== "pointerdown" && event.type !== "mousedown") return false;
            const ly = pos?.[1];
            // Whole row is the hit target, not just the little arrow — a 20px
            // strip with an 8px glyph in it is not a realistic click target.
            if (ly == null || ly < this._hitY[0] || ly > this._hitY[1]) return false;
            setCollapsed(node, label, !isCollapsed(node, label));
            onToggle();
            (mouseNode ?? node).setDirtyCanvas(true, true);
            // Nodes 2.0's WidgetLegacy bridge only repaints its canvas via the
            // widget's own triggerDraw() (set on it once mounted), not via
            // setDirtyCanvas — without this the arrow never flips even though
            // the state did change. Same fix as CraftKit's preset picker.
            this.triggerDraw?.();
            return true;
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
//
// Note this only ever hides — it never removes the widget from node.widgets.
// Removal would shift the indices that widget values are saved and restored
// by (§23), so a collapsed section is still a full-length widget array.
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

// setWidgetVisible alone shrinks a widget's computeSize to zero but leaves
// node.size untouched, so a node that grew to fit (say) the custom-sound
// fields stayed tall forever once they were hidden again. Recompute the
// height here, but keep whatever width the user dragged the node to —
// computeSize() returns the minimum width, which would undo a manual resize.
function resizeToFit(node) {
    const [w, h] = node.computeSize();
    node.setSize([Math.max(w, node.size?.[0] ?? w), h]);
    node.setDirtyCanvas(true, true);
}

// Repair a save/load round-trip that LiteGraph gets wrong on any node with
// JS-added widgets. serialize() writes widgets_values indexed over the FULL
// node.widgets array, putting a null placeholder at the index of every
// serialize:false widget (our dividers and buttons). configure() then reads
// that array back *sequentially*, skipping those same widgets — so every real
// widget after the first JS-only one is restored from the wrong slot.
// Confirmed live on a round-trip: a saved custom_sound_path landed in
// wait_for_click, turning a boolean into a truthy string, which would block
// the run forever on a Continue button the user never enabled.
//
// This is the load-time sibling of the positional-default bug in §23, and the
// same rule applies: fix it by index, never by reordering node.widgets.
function restoreWidgetValues(node, info) {
    const vals = info?.widgets_values;
    if (!Array.isArray(vals) || !node.widgets) return;

    const isSerialized = (w) => !(w.serialize === false || w.options?.serialize === false);
    const serializableIdx = node.widgets.map((w, i) => (isSerialized(w) ? i : -1)).filter((i) => i >= 0);
    if (!serializableIdx.length) return;

    // Two possible layouts. Index-aligned (what serialize() produces here) runs
    // to the last serializable widget, with nulls filling the JS-only slots;
    // trailing serialize:false widgets are dropped entirely. Compact is one
    // entry per serializable widget — that's what configure() already assumed,
    // so it got it right and there's nothing to repair.
    const indexAlignedLength = serializableIdx[serializableIdx.length - 1] + 1;
    if (vals.length !== indexAlignedLength || indexAlignedLength === serializableIdx.length) return;

    for (const i of serializableIdx) {
        if (vals[i] !== undefined) node.widgets[i].value = vals[i];
    }
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

    // A node that's actively waiting has to show its Continue button even if
    // the OPTIONS section is collapsed — otherwise the run sits blocked behind
    // a control the user can't see.
    node._smartQueueApplyVisibility?.();
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
        const applyVisibility = () => node._smartQueueApplyVisibility?.();
        addSeparator(node, NOTIFY_SECTION, NOTIFY_SECTION_FIRST_WIDGET, applyVisibility);
        addSeparator(node, WAIT_SECTION, WAIT_SECTION_FIRST_WIDGET, applyVisibility);

        const byName = (name) => node.widgets.find((w) => w.name === name);

        const waitForTempWidget = byName("wait_for_temp");
        const tempSubWidgets = ["target_temp_c", "poll_interval_seconds", "max_wait_seconds"]
            .map(byName)
            .filter(Boolean);

        const notifySoundWidget = byName("notify_sound");
        const notifySoundChoiceWidget = byName("notify_sound_choice");
        const customSoundPathWidget = byName("custom_sound_path");
        const notifyToastWidget = byName("notify_toast");
        const waitForClickWidget = byName("wait_for_click");

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

        // Section membership, resolved once here because the JS-only buttons
        // only exist by reference — they have no stable schema name to look up.
        const notifyMembers = [
            notifyToastWidget,
            notifySoundWidget,
            notifySoundChoiceWidget,
            customSoundPathWidget,
            browseBtn,
        ].filter(Boolean);
        const waitMembers = [
            byName("unload_models_before_wait"),
            byName("clear_cache_before_wait"),
            waitForClickWidget,
            continueBtn,
            cancelBtn,
        ].filter(Boolean);

        // ONE visibility pass for the whole node. Section collapse state and
        // the per-toggle conditions (temp fields only while wait_for_temp is
        // on, sound fields only while notify_sound is on, Continue/Cancel only
        // while wait_for_click is on) both feed the same decision — running
        // them as two independent updaters would let them fight, with
        // whichever ran last winning.
        const updateVisibility = () => {
            const notifyOpen = !isCollapsed(node, NOTIFY_SECTION);
            const waitOpen = !isCollapsed(node, WAIT_SECTION);
            const soundOn = !!notifySoundWidget?.value;
            const customSound = soundOn && notifySoundChoiceWidget?.value === "Custom...";
            const clickOn = !!waitForClickWidget?.value;
            const isWaiting = pending.has(String(node.id));

            for (const w of tempSubWidgets) setWidgetVisible(node, w, !!waitForTempWidget?.value);

            for (const w of notifyMembers) {
                let visible = notifyOpen;
                if (w === notifySoundChoiceWidget) visible = notifyOpen && soundOn;
                if (w === customSoundPathWidget || w === browseBtn) visible = notifyOpen && customSound;
                setWidgetVisible(node, w, visible);
            }

            for (const w of waitMembers) {
                // Continue/Cancel are meaningless unless wait_for_click is on,
                // and while the node is actually waiting they have to stay
                // reachable even with the section collapsed. The section's
                // schema widgets just follow the collapse state.
                const isButton = w === continueBtn || w === cancelBtn;
                const visible = isButton ? clickOn && (waitOpen || isWaiting) : waitOpen;
                setWidgetVisible(node, w, visible);
            }

            resizeToFit(node);
        };
        node._smartQueueApplyVisibility = updateVisibility;

        // Re-run the pass after any toggle that feeds into it, and after
        // configure() restores saved values + collapse state on workflow load.
        for (const w of [waitForTempWidget, notifySoundWidget, notifySoundChoiceWidget, waitForClickWidget]) {
            if (!w) continue;
            const orig = w.callback;
            w.callback = function (...args) {
                orig?.call(this, ...args);
                updateVisibility();
            };
        }
        const origOnConfigure = node.onConfigure;
        node.onConfigure = function (info, ...rest) {
            origOnConfigure?.call(this, info, ...rest);
            restoreWidgetValues(this, info);
            updateVisibility();
        };

        updateVisibility();
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
