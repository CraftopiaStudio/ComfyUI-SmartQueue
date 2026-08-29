import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "SmartQueue.Panel",
    settings: [
        {
            id: "SmartQueue.EnableAutopilot",
            name: "Turn on autopilot (GPU monitoring and pause rules)",
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
                    waiting = data.items.length;
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
                btn.title = data.is_paused
                    ? `Paused — ${data.reasons.join("; ")}. Click to resume.`
                    : "Pause queue (Smart Queue)";
                btn.classList.toggle("smart-queue-toolbar-btn-paused", data.is_paused);

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
                        <ul class="smart-queue-list" id="smart-queue-list"></ul>
                        <div class="smart-queue-section-title">History</div>
                        <ul class="smart-queue-list smart-queue-history" id="smart-queue-history"></ul>
                    </div>
                `;

                const panel = el.querySelector("#smart-queue-panel");

                panel.querySelector(".smart-queue-settings-btn").addEventListener("click", () => {
                    app.extensionManager.command.execute("Comfy.ShowSettingsDialog");
                });

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
                    try {
                        const res = await fetch("/smart_queue/queue");
                        const data = await res.json();
                        const listEl = panel.querySelector("#smart-queue-list");
                        listEl.innerHTML = "";
                        if (data.items.length === 0) {
                            listEl.innerHTML = '<li class="smart-queue-empty">Nothing queued</li>';
                        }
                        for (const item of data.items) {
                            const li = document.createElement("li");
                            li.draggable = true;
                            li.dataset.promptId = item.prompt_id;
                            li.classList.toggle("smart-queue-item-held", item.status === "held");
                            if (item.status === "held") {
                                const badge = document.createElement("span");
                                badge.className = "smart-queue-held-badge";
                                badge.textContent = "held";
                                li.appendChild(badge);
                            }
                            li.appendChild(document.createTextNode(item.name));
                            listEl.appendChild(li);
                        }
                    } catch (err) {
                        console.error("[Smart Queue] queue fetch failed:", err);
                    }
                }

                async function refreshHistory() {
                    try {
                        const res = await fetch("/smart_queue/history");
                        const data = await res.json();
                        const listEl = panel.querySelector("#smart-queue-history");
                        listEl.innerHTML = "";
                        if (data.items.length === 0) {
                            listEl.innerHTML = '<li class="smart-queue-empty">No history yet</li>';
                        }
                        for (const item of data.items) {
                            const li = document.createElement("li");
                            li.textContent = item.name;
                            listEl.appendChild(li);
                        }
                    } catch (err) {
                        console.error("[Smart Queue] history fetch failed:", err);
                    }
                }

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
                };
            },
        });
    },
});
