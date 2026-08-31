import { app } from "../../scripts/app.js";
import { formatDuration } from "./format_duration.js";

app.registerExtension({
    name: "SmartQueue.Panel",
    settings: [
        {
            id: "SmartQueue.EnableAutopilot",
            name: "Turn on autopilot (GPU monitoring and pause rules)",
            tooltip: "Off removes the sidebar panel and toolbar pause button entirely (not just hides them) and stops all background queue tracking — only the Smart Cooldown & Pause node keeps working. Re-enabling here brings everything back within about 10 seconds, no restart needed.",
            type: "boolean",
            defaultValue: true,
            category: ["SmartQueue", "1. Autopilot", "Turn on autopilot (GPU monitoring and pause rules)"],
        },
        // Settings render in reverse of this array's order within a category
        // section, so each group below is listed bottom-to-top of how it
        // should appear on screen. The 3rd category element must be unique
        // per setting (it's used as the item's dedup key) — reusing `name`
        // for it happens to also be a legible key.
        {
            id: "SmartQueue.ResumeTempC",
            name: "Resume once it's cooled back down to this temperature (°C)",
            type: "number",
            defaultValue: 72.0,
            category: ["SmartQueue", "2. Temperature", "Resume once it's cooled back down to this temperature (°C)"],
        },
        {
            id: "SmartQueue.PauseTempC",
            name: "Pause once the GPU hits this temperature (°C)",
            type: "number",
            defaultValue: 80.0,
            category: ["SmartQueue", "2. Temperature", "Pause once the GPU hits this temperature (°C)"],
        },
        {
            id: "SmartQueue.TempRuleEnabled",
            name: "Pause the queue automatically if your GPU gets too hot",
            type: "boolean",
            defaultValue: false,
            category: ["SmartQueue", "2. Temperature", "Pause the queue automatically if your GPU gets too hot"],
        },
        {
            id: "SmartQueue.MinFreeVramMb",
            name: "Pause when free VRAM drops below this (MB)",
            type: "number",
            defaultValue: 1024,
            category: ["SmartQueue", "3. VRAM", "Pause when free VRAM drops below this (MB)"],
        },
        {
            id: "SmartQueue.VramRuleEnabled",
            name: "Pause the queue automatically when VRAM is running low",
            type: "boolean",
            defaultValue: false,
            category: ["SmartQueue", "3. VRAM", "Pause the queue automatically when VRAM is running low"],
        },
        {
            id: "SmartQueue.JobCountBreakMinutes",
            name: "How long that break should last (minutes)",
            type: "number",
            defaultValue: 5,
            category: ["SmartQueue", "4. Job count", "How long that break should last (minutes)"],
        },
        {
            id: "SmartQueue.MaxJobsBeforePause",
            name: "How many jobs before taking that break",
            type: "number",
            defaultValue: 20,
            category: ["SmartQueue", "4. Job count", "How many jobs before taking that break"],
        },
        {
            id: "SmartQueue.JobCountRuleEnabled",
            name: "Give the GPU a break after a batch of jobs",
            type: "boolean",
            defaultValue: false,
            category: ["SmartQueue", "4. Job count", "Give the GPU a break after a batch of jobs"],
        },
        {
            id: "SmartQueue.HistoryRetentionDays",
            name: "Delete history older than this many days (0 = never)",
            type: "number",
            defaultValue: 30,
            category: ["SmartQueue", "5. History", "Delete history older than this many days (0 = never)"],
        },
        {
            id: "SmartQueue.FreeVramOnPause",
            name: "Free VRAM when paused (unload models + clear cache, once the running job finishes)",
            type: "boolean",
            defaultValue: false,
            category: ["SmartQueue", "6. Pause", "Free VRAM when paused (unload models + clear cache, once the running job finishes)"],
        },
    ],
    async setup() {
        if (!document.getElementById("smart-queue-stylesheet")) {
            const link = document.createElement("link");
            link.id = "smart-queue-stylesheet";
            link.rel = "stylesheet";
            link.href = new URL("smart_queue.css", import.meta.url).href;
            document.head.appendChild(link);
        }

        async function syncSettingsToBackend() {
            const payload = {
                master_enabled: app.extensionManager.setting.get("SmartQueue.EnableAutopilot"),
                temp_rule_enabled: app.extensionManager.setting.get("SmartQueue.TempRuleEnabled"),
                pause_temp_c: app.extensionManager.setting.get("SmartQueue.PauseTempC"),
                resume_temp_c: app.extensionManager.setting.get("SmartQueue.ResumeTempC"),
                vram_rule_enabled: app.extensionManager.setting.get("SmartQueue.VramRuleEnabled"),
                min_free_vram_mb: app.extensionManager.setting.get("SmartQueue.MinFreeVramMb"),
                job_count_rule_enabled: app.extensionManager.setting.get("SmartQueue.JobCountRuleEnabled"),
                max_jobs_before_pause: app.extensionManager.setting.get("SmartQueue.MaxJobsBeforePause"),
                job_count_break_minutes: app.extensionManager.setting.get("SmartQueue.JobCountBreakMinutes"),
                history_retention_days: app.extensionManager.setting.get("SmartQueue.HistoryRetentionDays"),
                free_vram_on_pause: app.extensionManager.setting.get("SmartQueue.FreeVramOnPause"),
            };
            try {
                await fetch("/smart_queue/settings", {
                    method: "POST",
                    body: JSON.stringify(payload),
                    headers: { "Content-Type": "application/json" },
                });
            } catch (err) {
                console.error("[Smart Queue] settings sync failed:", err);
            }
        }

        await syncSettingsToBackend();
        setInterval(syncSettingsToBackend, 10000);

        // Tag the currently-open workflow tab's name onto the graph's own
        // `extra` bag right before ComfyUI serializes and submits it, so
        // queue_tracker.extract_job_name (backend/queue_tracker.py) picks
        // it up on its very next tick via the exact `workflow.extra.
        // workflow_name` field it already checks — no new endpoint, no
        // race against that tick, no backend change needed. ComfyUI's own
        // /prompt payload never includes the tab title on its own (that's
        // pure frontend state, tracked by app.extensionManager.workflow,
        // not part of the graph), which is why every job used to fall
        // back to a timestamp-only name regardless of which workflow tab
        // it came from. Independent of the "Enable Autopilot" toggle below
        // (naming isn't a GPU-polling concern) and of the MCP/API queuing
        // path (a /prompt call made outside the browser has no open tab to
        // read a name from — same timestamp fallback as before).
        const originalQueuePrompt = app.queuePrompt.bind(app);
        app.queuePrompt = async (...args) => {
            let tagged = false;
            try {
                const workflowName = app.extensionManager?.workflow?.activeWorkflow?.filename;
                if (workflowName && app.graph?.extra) {
                    // Matches queue_tracker.extract_job_name's own
                    // "%Y-%m-%d %H:%M:%S" fallback format — the panel shows
                    // no other timestamp next to a job's name, and history
                    // can span up to the configured retention window (§17,
                    // default 30 days), so a time-only suffix would be
                    // ambiguous about which day a job ran.
                    const now = new Date();
                    const pad = (n) => String(n).padStart(2, "0");
                    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}, ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
                    app.graph.extra.workflow_name = `${workflowName} [${stamp}]`;
                    tagged = true;
                }
            } catch (err) {
                console.error("[Smart Queue] failed to tag workflow name before queuing:", err);
            }
            try {
                return await originalQueuePrompt(...args);
            } finally {
                if (tagged) delete app.graph.extra.workflow_name;
            }
        };

        const enabled = app.extensionManager.setting.get("SmartQueue.EnableAutopilot");
        if (!enabled) {
            console.log("[Smart Queue] Autopilot panel disabled via settings.");
            return;
        }

        const hasCrystools = app.extensions?.some((ext) => ext.name?.toLowerCase().includes("crystools"));

        // A small square icon button inserted as a real sibling next to
        // ComfyUI's own Run button group, matching the size/style of its
        // "Cancel current run" button. Because it's a genuine DOM child of
        // that flex row (not a position:fixed overlay with guessed pixel
        // coordinates), the browser's own flex layout makes room for it —
        // no manual placement math, and nothing gets covered.
        // Risk: that row is Vue-managed, so a re-render could in principle
        // drop this manually-inserted node; a periodic check re-inserts it
        // if that ever happens.
        let manualPaused = false;
        // null until the first status poll resolves, so we never fire a
        // toast for the state ComfyUI happened to be in on page load.
        let lastEffectivePaused = null;

        async function notifyPauseStateChange(isPaused) {
            if (!app.extensionManager?.toast) return;
            if (isPaused) {
                let waiting = 0;
                try {
                    const res = await fetch("/smart_queue/queue");
                    const data = await res.json();
                    // Filter out the currently-running item(s) using the same
                    // "running" status overlay get_queue already applies
                    // (backend/routes.py) instead of subtracting a
                    // separately-fetched running_count: that subtraction
                    // raced two endpoints with different lag against each
                    // other (running_count reads PromptQueue live; a row
                    // only exists in /smart_queue/queue once queue_tracker's
                    // ~5s tick has synced it in), so a job that started
                    // running just before its own row synced could wrongly
                    // zero out a genuinely-held job's count. Filtering a
                    // single response has no such race — both the item list
                    // and its "running" tag come from the same request.
                    waiting = data.items.filter((item) => item.status !== "running").length;
                } catch (err) {
                    console.error("[Smart Queue] queue fetch for pause toast failed:", err);
                }
                const jobWord = waiting === 1 ? "job" : "jobs";
                app.extensionManager.toast.add({
                    severity: "warn",
                    summary: "Smart Queue paused",
                    detail: `${waiting} ${jobWord} waiting. Manage them in the sidebar.`,
                });
            } else {
                app.extensionManager.toast.add({
                    severity: "success",
                    summary: "Smart Queue resumed",
                    detail: "The queue is running again.",
                });
            }
        }

        function findToolbarRow() {
            const toggle = document.querySelector('[data-testid="queue-overlay-toggle"]');
            return toggle ? toggle.parentElement : null;
        }

        function ensureToolbarPauseButton() {
            const row = findToolbarRow();
            if (!row) return null;

            let btn = document.getElementById("smart-queue-toolbar-btn");
            if (btn && row.contains(btn)) return btn;

            btn = document.createElement("button");
            btn.id = "smart-queue-toolbar-btn";
            btn.className = "smart-queue-toolbar-btn";
            btn.setAttribute("aria-label", "Pause queue (Smart Queue)");
            btn.setAttribute("data-pd-tooltip", "true");
            btn.title = "Pause queue (Smart Queue)";
            btn.textContent = "⏸";

            btn.addEventListener("click", async () => {
                manualPaused = !manualPaused;
                try {
                    await fetch("/smart_queue/manual_pause", {
                        method: "POST",
                        body: JSON.stringify({ paused: manualPaused }),
                        headers: { "Content-Type": "application/json" },
                    });
                } catch (err) {
                    console.error("[Smart Queue] manual pause toggle failed:", err);
                }
                await refreshToolbarButton();
            });

            // Inserted right after the batch-count/Run button group (the
            // row's first child) so it reads as "next to Run".
            const runGroup = row.children[0];
            if (runGroup && runGroup.nextSibling) {
                row.insertBefore(btn, runGroup.nextSibling);
            } else {
                row.insertBefore(btn, row.firstChild);
            }
            return btn;
        }

        async function refreshToolbarButton() {
            const btn = ensureToolbarPauseButton();
            if (!btn) return;
            try {
                const res = await fetch("/smart_queue/status");
                const data = await res.json();
                manualPaused = data.manual_paused;
                // Icon stays the pause glyph regardless of state — swapping to a
                // play icon read as "click to start rendering" instead of "we're
                // paused, click to resume", which was confusing. The highlighted
                // background (smart-queue-toolbar-btn-paused) carries the state.
                btn.textContent = "⏸";
                // Label/highlight follow manual_paused, not the effective
                // is_paused (autopilot OR manual) — the click handler only
                // ever toggles manual_paused, so driving the tooltip from the
                // effective state used to tell the user a click would resume
                // when it actually engaged an additional manual pause on top
                // of an autopilot one. The sidebar's status line already
                // surfaces autopilot-only pauses with reasons.
                if (data.manual_paused) {
                    btn.title = `Paused — ${data.reasons.join("; ")}. Click to resume.`;
                } else if (data.autopilot_paused) {
                    btn.title = `Autopilot paused — ${data.reasons.join("; ")}. Click to pause manually too.`;
                } else {
                    btn.title = "Pause queue (Smart Queue)";
                }
                btn.classList.toggle("smart-queue-toolbar-btn-paused", data.manual_paused);

                if (lastEffectivePaused !== null && data.is_paused !== lastEffectivePaused) {
                    notifyPauseStateChange(data.is_paused);
                }
                lastEffectivePaused = data.is_paused;
            } catch (err) {
                console.error("[Smart Queue] status fetch failed:", err);
            }
        }

        setInterval(refreshToolbarButton, 3000);
        refreshToolbarButton();

        app.extensionManager.registerSidebarTab({
            id: "smart-queue",
            icon: "pi pi-pause-circle",
            title: "Smart Queue",
            tooltip: "Smart Queue: GPU autopilot + render queue",
            type: "custom",
            render: (el) => {
                el.innerHTML = `
                    <div id="smart-queue-panel">
                        <div class="smart-queue-status-row">
                            <div class="smart-queue-status">Smart Queue: idle</div>
                            <button type="button" class="smart-queue-settings-btn" title="Smart Queue settings" aria-label="Smart Queue settings">⚙</button>
                        </div>
                        ${hasCrystools ? "" : '<div class="smart-queue-gpu-readout"></div>'}
                        <div class="smart-queue-section-title">Pending / running</div>
                        <input type="text" class="smart-queue-search" id="smart-queue-search" placeholder="Search queue…">
                        <ul class="smart-queue-list" id="smart-queue-list"></ul>
                        <div class="smart-queue-section-title">History</div>
                        <input type="text" class="smart-queue-search" id="smart-queue-history-search" placeholder="Search history…">
                        <ul class="smart-queue-list smart-queue-history" id="smart-queue-history"></ul>
                    </div>
                `;

                const panel = el.querySelector("#smart-queue-panel");

                panel.querySelector(".smart-queue-settings-btn").addEventListener("click", () => {
                    app.extensionManager.command.execute("Comfy.ShowSettingsDialog");
                });

                // ── Rename (adapted from comfyui-workfloworganizer's
                // inlineRenameInTree: swap the label for an <input>, Enter
                // commits, Escape/blur cancels) ──────────────────────────
                // The periodic refreshQueueList (every 5s) rebuilds the list's
                // innerHTML from scratch, which would destroy an in-progress
                // rename <input> mid-edit (losing focus fires blur → cancel).
                // Guard it with this flag while an edit is open.
                let renameInProgress = false;

                function startInlineRename(nameSpan, promptId, currentName) {
                    const originalText = nameSpan.textContent;
                    let committed = false;
                    renameInProgress = true;

                    const input = document.createElement("input");
                    input.className = "smart-queue-rename-input";
                    input.value = currentName;
                    nameSpan.textContent = "";
                    nameSpan.appendChild(input);

                    const restore = (text) => { nameSpan.textContent = text; };

                    const commit = async () => {
                        if (committed) return;
                        committed = true;
                        renameInProgress = false;
                        const value = input.value.trim();
                        if (!value || value === currentName) { restore(originalText); return; }
                        restore(value);
                        try {
                            await fetch("/smart_queue/rename", {
                                method: "POST",
                                body: JSON.stringify({ prompt_id: promptId, name: value }),
                                headers: { "Content-Type": "application/json" },
                            });
                        } catch (err) {
                            console.error("[Smart Queue] rename failed:", err);
                            restore(originalText);
                        }
                    };

                    const cancel = () => {
                        if (committed) return;
                        committed = true;
                        renameInProgress = false;
                        restore(originalText);
                    };

                    input.addEventListener("mousedown", (e) => e.stopPropagation());
                    input.addEventListener("click", (e) => e.stopPropagation());
                    input.addEventListener("keydown", async (e) => {
                        if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); await commit(); }
                        else if (e.key === "Escape") { e.stopPropagation(); cancel(); }
                    });
                    input.addEventListener("blur", cancel);

                    requestAnimationFrame(() => { input.focus(); input.select(); });
                }

                // ── Multi-select (ctrl/shift-click) + right-click context menu
                // + bulk cancel/requeue (adapted from comfyui-workfloworganizer's
                // selectedPaths/selection-bar/context-menu pattern, scoped to
                // prompt_ids instead of file paths) ─────────────────────────
                const selectedPromptIds = new Set();
                let selectionAnchorId = null;
                let orderedIds = [];
                let selectionBarEl = null;
                let contextMenuEl = null;

                function clearSelection() {
                    if (!selectedPromptIds.size) return;
                    selectedPromptIds.clear();
                    selectionAnchorId = null;
                    applySelectionStyles();
                    updateSelectionBar();
                }

                function applySelectionStyles() {
                    panel.querySelectorAll("#smart-queue-list > li[data-prompt-id]").forEach((li) => {
                        li.classList.toggle("smart-queue-item-selected", selectedPromptIds.has(li.dataset.promptId));
                    });
                }

                function selectRange(fromId, toId) {
                    const i = orderedIds.indexOf(fromId);
                    const j = orderedIds.indexOf(toId);
                    if (i === -1 || j === -1) { selectedPromptIds.add(toId); return; }
                    const [lo, hi] = i <= j ? [i, j] : [j, i];
                    for (let k = lo; k <= hi; k++) selectedPromptIds.add(orderedIds[k]);
                }

                function removeContextMenu() {
                    if (contextMenuEl) { contextMenuEl.remove(); contextMenuEl = null; }
                }

                function makeContextItem(label, onPick, danger = false) {
                    const el = document.createElement("div");
                    el.className = "smart-queue-context-item" + (danger ? " smart-queue-context-danger" : "");
                    el.textContent = label;
                    el.addEventListener("mousedown", (ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        onPick();
                    });
                    return el;
                }

                function showQueueContextMenu(e, promptId, name, nameSpan) {
                    removeContextMenu();
                    e.preventDefault();

                    // Right-clicking outside the current selection selects just
                    // this item; right-clicking inside it keeps the selection.
                    if (!selectedPromptIds.has(promptId)) {
                        selectedPromptIds.clear();
                        selectedPromptIds.add(promptId);
                        selectionAnchorId = promptId;
                        applySelectionStyles();
                        updateSelectionBar();
                    }
                    const multi = selectedPromptIds.size > 1;

                    const menu = document.createElement("div");
                    menu.className = "smart-queue-context-menu";

                    if (!multi) {
                        menu.appendChild(makeContextItem("Rename", () => {
                            removeContextMenu();
                            startInlineRename(nameSpan, promptId, name);
                        }));
                    }
                    menu.appendChild(makeContextItem(multi ? `Cancel ${selectedPromptIds.size}` : "Cancel", () => {
                        removeContextMenu();
                        runBulkCancel(false);
                    }, true));
                    menu.appendChild(makeContextItem(multi ? `Cancel & Requeue ${selectedPromptIds.size}` : "Cancel & Requeue", () => {
                        removeContextMenu();
                        runBulkCancel(true);
                    }));

                    document.body.appendChild(menu);
                    menu.style.left = e.clientX + "px";
                    menu.style.top = e.clientY + "px";
                    contextMenuEl = menu;

                    const closeHandler = (ev) => {
                        if (menu.contains(ev.target)) return;
                        removeContextMenu();
                        document.removeEventListener("mousedown", closeHandler);
                    };
                    setTimeout(() => document.addEventListener("mousedown", closeHandler), 0);
                }

                // Escape closes an open context menu first; with none open, it
                // clears the current selection (same two-step precedence as
                // comfyui-workfloworganizer).
                const escapeHandler = (e) => {
                    if (e.key !== "Escape") return;
                    if (contextMenuEl) { removeContextMenu(); return; }
                    clearSelection();
                };
                document.addEventListener("keydown", escapeHandler);

                function updateSelectionBar() {
                    const n = selectedPromptIds.size;
                    if (n === 0) {
                        if (selectionBarEl) { selectionBarEl.remove(); selectionBarEl = null; }
                        return;
                    }
                    if (!selectionBarEl) {
                        selectionBarEl = document.createElement("div");
                        selectionBarEl.className = "smart-queue-selection-bar";
                        selectionBarEl.innerHTML = `
                            <span class="smart-queue-sel-count"></span>
                            <button type="button" class="smart-queue-sel-btn" data-action="cancel">Cancel</button>
                            <button type="button" class="smart-queue-sel-btn" data-action="requeue">Cancel &amp; Requeue</button>
                        `;
                        document.body.appendChild(selectionBarEl);
                        selectionBarEl.querySelector('[data-action="cancel"]').addEventListener("click", () => runBulkCancel(false));
                        selectionBarEl.querySelector('[data-action="requeue"]').addEventListener("click", () => runBulkCancel(true));
                    }
                    selectionBarEl.querySelector(".smart-queue-sel-count").textContent = `${n} selected`;
                }

                async function runBulkCancel(requeue) {
                    const prompt_ids = [...selectedPromptIds];
                    selectedPromptIds.clear();
                    updateSelectionBar();
                    try {
                        const res = await fetch("/smart_queue/cancel", {
                            method: "POST",
                            body: JSON.stringify({ prompt_ids, requeue }),
                            headers: { "Content-Type": "application/json" },
                        });
                        const data = await res.json();
                        if (app.extensionManager?.toast) {
                            app.extensionManager.toast.add({
                                severity: "success",
                                summary: requeue ? "Requeued" : "Cancelled",
                                detail: requeue
                                    ? `${data.requeued} job(s) moved to the back.`
                                    : `${data.cancelled} job(s) cancelled.`,
                            });
                        }
                    } catch (err) {
                        console.error("[Smart Queue] bulk cancel failed:", err);
                    }
                    await refreshQueueList();
                }

                // ── Drag-to-reorder (adapted from comfyui-workfloworganizer's
                // dragData-based native HTML5 drag/drop) — this actually
                // renumbers ComfyUI's real queue via /smart_queue/reorder,
                // not just the panel's own display order. ──────────────────
                let dragSourceId = null;

                async function refreshStatus() {
                    try {
                        const res = await fetch("/smart_queue/status");
                        const data = await res.json();
                        const statusEl = panel.querySelector(".smart-queue-status");
                        statusEl.textContent = data.is_paused
                            ? `Paused — ${data.reasons.join("; ")}`
                            : "Running";
                        statusEl.classList.toggle("smart-queue-paused", data.is_paused);

                        if (!hasCrystools) {
                            const gpuEl = panel.querySelector(".smart-queue-gpu-readout");
                            if (gpuEl) {
                                const parts = [];
                                if (data.temp_c != null) parts.push(`${data.temp_c.toFixed(0)}°C`);
                                if (data.vram_used_mb != null && data.vram_total_mb != null) {
                                    parts.push(`${(data.vram_used_mb / 1024).toFixed(1)} / ${(data.vram_total_mb / 1024).toFixed(1)} GB VRAM`);
                                }
                                gpuEl.textContent = parts.join("  ·  ");
                            }
                        }
                    } catch (err) {
                        console.error("[Smart Queue] status fetch failed:", err);
                    }
                }

                async function refreshQueueList() {
                    if (renameInProgress) return;
                    try {
                        const q = panel.querySelector("#smart-queue-search").value.trim();
                        const url = q ? `/smart_queue/queue?name=${encodeURIComponent(q)}` : "/smart_queue/queue";
                        const res = await fetch(url);
                        const data = await res.json();
                        const listEl = panel.querySelector("#smart-queue-list");
                        listEl.innerHTML = "";
                        orderedIds = data.items.map((i) => i.prompt_id);
                        if (data.items.length === 0) {
                            listEl.innerHTML = '<li class="smart-queue-empty">Nothing queued</li>';
                        }
                        for (const item of data.items) {
                            const li = document.createElement("li");
                            // A running job can never actually be reordered —
                            // reorder_pending_queue only knows about PromptQueue's
                            // pending heap, so a running ID silently drops out of
                            // any drag that includes it. Don't offer a drag the
                            // server will just ignore.
                            li.draggable = item.status !== "running";
                            li.dataset.promptId = item.prompt_id;
                            li.classList.toggle("smart-queue-item-held", item.status === "held");
                            li.classList.toggle("smart-queue-item-running", item.status === "running");
                            li.classList.toggle("smart-queue-item-selected", selectedPromptIds.has(item.prompt_id));

                            li.addEventListener("click", (e) => {
                                if (e.ctrlKey || e.metaKey) {
                                    e.stopPropagation();
                                    if (selectedPromptIds.has(item.prompt_id)) selectedPromptIds.delete(item.prompt_id);
                                    else selectedPromptIds.add(item.prompt_id);
                                    selectionAnchorId = item.prompt_id;
                                    applySelectionStyles();
                                    updateSelectionBar();
                                } else if (e.shiftKey && selectionAnchorId) {
                                    e.stopPropagation();
                                    selectRange(selectionAnchorId, item.prompt_id);
                                    applySelectionStyles();
                                    updateSelectionBar();
                                } else {
                                    clearSelection();
                                    selectionAnchorId = item.prompt_id;
                                }
                            });
                            li.addEventListener("contextmenu", (e) => {
                                showQueueContextMenu(e, item.prompt_id, item.name, nameSpan);
                            });

                            if (item.status === "held") {
                                const badge = document.createElement("span");
                                badge.className = "smart-queue-held-badge";
                                badge.textContent = "held";
                                li.appendChild(badge);
                            } else if (item.status === "running") {
                                const badge = document.createElement("span");
                                badge.className = "smart-queue-running-badge";
                                badge.textContent = "running";
                                li.appendChild(badge);
                            }

                            const nameSpan = document.createElement("span");
                            nameSpan.className = "smart-queue-item-name";
                            nameSpan.textContent = item.name;
                            nameSpan.addEventListener("dblclick", (e) => {
                                e.stopPropagation();
                                startInlineRename(nameSpan, item.prompt_id, item.name);
                            });
                            li.appendChild(nameSpan);

                            li.addEventListener("dragstart", () => {
                                dragSourceId = item.prompt_id;
                                li.classList.add("smart-queue-dragging");
                            });
                            li.addEventListener("dragend", () => {
                                li.classList.remove("smart-queue-dragging");
                                dragSourceId = null;
                                listEl.querySelectorAll(".smart-queue-drop-target").forEach((el) => el.classList.remove("smart-queue-drop-target"));
                            });
                            li.addEventListener("dragover", (e) => {
                                if (!dragSourceId) return;
                                const moving = (selectedPromptIds.has(dragSourceId) && selectedPromptIds.size > 1)
                                    ? selectedPromptIds
                                    : new Set([dragSourceId]);
                                if (moving.has(item.prompt_id)) return;
                                e.preventDefault();
                                li.classList.add("smart-queue-drop-target");
                            });
                            li.addEventListener("dragleave", () => li.classList.remove("smart-queue-drop-target"));
                            li.addEventListener("drop", async (e) => {
                                e.preventDefault();
                                li.classList.remove("smart-queue-drop-target");
                                if (!dragSourceId) return;
                                // Dragging a row that's part of a multi-selection moves the
                                // whole selected group together (preserving their relative
                                // order), not just the row the user happened to grab — same
                                // as comfyui-workfloworganizer's multi-drag behavior.
                                const sourceId = dragSourceId;
                                dragSourceId = null;
                                // data.items holds only the matches while the search box
                                // has text in it. Sending that as the complete order made
                                // the server treat every non-matching job as a leftover and
                                // append it *after* the matches — silently promoting the
                                // search results to the front of the real queue — and left
                                // duplicate order_index values behind in SQLite. Reorder
                                // against the full queue instead.
                                let ids = data.items.map((i) => i.prompt_id);
                                if (panel.querySelector("#smart-queue-search").value.trim()) {
                                    try {
                                        const fullRes = await fetch("/smart_queue/queue");
                                        ids = (await fullRes.json()).items.map((i) => i.prompt_id);
                                    } catch (err) {
                                        console.error("[Smart Queue] reorder aborted, could not read the unfiltered queue:", err);
                                        return;
                                    }
                                }
                                const movingIds = (selectedPromptIds.has(sourceId) && selectedPromptIds.size > 1)
                                    ? ids.filter((id) => selectedPromptIds.has(id))
                                    : [sourceId];
                                if (movingIds.includes(item.prompt_id)) return;
                                const remaining = ids.filter((id) => !movingIds.includes(id));
                                const toIdx = remaining.indexOf(item.prompt_id);
                                if (toIdx === -1) return;
                                remaining.splice(toIdx, 0, ...movingIds);
                                try {
                                    await fetch("/smart_queue/reorder", {
                                        method: "POST",
                                        body: JSON.stringify({ ordered_prompt_ids: remaining }),
                                        headers: { "Content-Type": "application/json" },
                                    });
                                } catch (err) {
                                    console.error("[Smart Queue] reorder failed:", err);
                                }
                                await refreshQueueList();
                            });

                            listEl.appendChild(li);
                        }
                    } catch (err) {
                        console.error("[Smart Queue] queue fetch failed:", err);
                    }
                }

                async function restoreWorkflowFromHistory(item) {
                    if (!item.workflow_json) return;
                    try {
                        const workflow = JSON.parse(item.workflow_json);
                        await app.loadGraphData(workflow);
                    } catch (err) {
                        console.error("[Smart Queue] failed to restore workflow from history thumbnail:", err);
                    }
                }

                let lastHistorySignature = null;
                async function refreshHistory() {
                    try {
                        const q = panel.querySelector("#smart-queue-history-search").value.trim();
                        const url = q ? `/smart_queue/history?name=${encodeURIComponent(q)}` : "/smart_queue/history";
                        const res = await fetch(url);
                        const data = await res.json();
                        // Rebuilding the list re-creates every <video> thumb from
                        // scratch, which restarts its frame load and shows up as
                        // a flicker every poll — skip the rebuild when nothing
                        // actually changed since the last fetch.
                        const signature = JSON.stringify(data.items);
                        if (signature === lastHistorySignature) return;
                        lastHistorySignature = signature;
                        const listEl = panel.querySelector("#smart-queue-history");
                        listEl.innerHTML = "";
                        if (data.items.length === 0) {
                            listEl.innerHTML = '<li class="smart-queue-empty">No history yet</li>';
                        }
                        for (const item of data.items) {
                            const li = document.createElement("li");
                            if (item.workflow_json) {
                                li.addEventListener("contextmenu", (e) => {
                                    removeContextMenu();
                                    e.preventDefault();
                                    const menu = document.createElement("div");
                                    menu.className = "smart-queue-context-menu";
                                    menu.appendChild(makeContextItem("Load workflow", () => {
                                        removeContextMenu();
                                        restoreWorkflowFromHistory(item);
                                    }));
                                    document.body.appendChild(menu);
                                    menu.style.left = e.clientX + "px";
                                    menu.style.top = e.clientY + "px";
                                    contextMenuEl = menu;
                                    const closeHandler = (ev) => {
                                        if (menu.contains(ev.target)) return;
                                        removeContextMenu();
                                        document.removeEventListener("mousedown", closeHandler);
                                    };
                                    setTimeout(() => document.addEventListener("mousedown", closeHandler), 0);
                                });
                            }
                            if (item.thumbnail_path) {
                                // <img> silently renders nothing for a video
                                // filename (SaveVideo outputs land in the same
                                // "images" history key as SaveImage, just with
                                // a .mp4/.webm name) — a <video> element is
                                // needed to get a frame preview out of it.
                                const filename = new URLSearchParams(item.thumbnail_path).get("filename") || "";
                                const isVideo = /\.(mp4|webm|mov|mkv|avi)$/i.test(filename);
                                const media = document.createElement(isVideo ? "video" : "img");
                                media.className = "smart-queue-thumb";
                                media.src = `/view?${item.thumbnail_path}`;
                                if (isVideo) {
                                    media.muted = true;
                                    media.loop = true;
                                    media.playsInline = true;
                                    media.preload = "metadata";
                                } else {
                                    media.loading = "lazy";
                                }
                                media.title = item.workflow_json ? "Click to restore this workflow" : "";
                                if (item.workflow_json) {
                                    media.addEventListener("click", () => restoreWorkflowFromHistory(item));
                                }
                                li.appendChild(media);
                            }
                            const nameEl = document.createElement("span");
                            nameEl.className = "smart-queue-history-name";
                            nameEl.textContent = item.name;
                            li.appendChild(nameEl);
                            const durationText = formatDuration(item.duration_seconds);
                            if (durationText) {
                                const durationEl = document.createElement("span");
                                durationEl.className = "smart-queue-duration";
                                durationEl.textContent = durationText;
                                li.appendChild(durationEl);
                            }
                            listEl.appendChild(li);
                        }
                    } catch (err) {
                        console.error("[Smart Queue] history fetch failed:", err);
                    }
                }

                let searchDebounce;
                panel.querySelector("#smart-queue-search").addEventListener("input", () => {
                    clearTimeout(searchDebounce);
                    searchDebounce = setTimeout(refreshQueueList, 250);
                });
                let historySearchDebounce;
                panel.querySelector("#smart-queue-history-search").addEventListener("input", () => {
                    clearTimeout(historySearchDebounce);
                    historySearchDebounce = setTimeout(refreshHistory, 250);
                });

                const statusTimer = setInterval(refreshStatus, 3000);
                const queueTimer = setInterval(refreshQueueList, 5000);
                const historyTimer = setInterval(refreshHistory, 5000);
                refreshStatus();
                refreshQueueList();
                refreshHistory();

                return () => {
                    clearInterval(statusTimer);
                    clearInterval(queueTimer);
                    clearInterval(historyTimer);
                    if (selectionBarEl) { selectionBarEl.remove(); selectionBarEl = null; }
                    removeContextMenu();
                    document.removeEventListener("keydown", escapeHandler);
                };
            },
        });
    },
});
