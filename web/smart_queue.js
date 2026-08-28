import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "SmartQueue.Panel",
    settings: [
        {
            id: "SmartQueue.EnableAutopilot",
            name: "Enable Autopilot Queue Panel",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.TempRuleEnabled",
            name: "Autopilot: pause on high temperature",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.VramRuleEnabled",
            name: "Autopilot: pause on low free VRAM",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.PauseTempC",
            name: "Autopilot: pause temperature (C)",
            type: "number",
            defaultValue: 80.0,
        },
        {
            id: "SmartQueue.ResumeTempC",
            name: "Autopilot: resume temperature (C)",
            type: "number",
            defaultValue: 72.0,
        },
        {
            id: "SmartQueue.JobCountRuleEnabled",
            name: "Autopilot: pause after N jobs",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.MinFreeVramMb",
            name: "Autopilot: minimum free VRAM (MB)",
            type: "number",
            defaultValue: 1024,
        },
        {
            id: "SmartQueue.MaxJobsBeforePause",
            name: "Autopilot: jobs before forced cooldown",
            type: "number",
            defaultValue: 20,
        },
    ],
    async setup() {
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

        const panel = document.createElement("div");
        panel.id = "smart-queue-panel";
        panel.innerHTML = `
            <div class="smart-queue-status">Smart Queue: idle</div>
            ${hasCrystools ? "" : '<div class="smart-queue-gpu-readout"></div>'}
            <ul class="smart-queue-list" id="smart-queue-list"></ul>
        `;

        const sidebar = document.querySelector(".comfyui-body-bottom") || document.querySelector(".comfy-menu");
        if (sidebar) {
            sidebar.appendChild(panel);
        }

        async function refreshStatus() {
            try {
                const res = await fetch("/smart_queue/status");
                const data = await res.json();
                const statusEl = panel.querySelector(".smart-queue-status");
                statusEl.textContent = data.is_paused
                    ? `Smart Queue: paused — ${data.reasons.join("; ")}`
                    : "Smart Queue: running";
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
                for (const item of data.items) {
                    const li = document.createElement("li");
                    li.draggable = true;
                    li.dataset.promptId = item.prompt_id;
                    li.textContent = item.name;
                    listEl.appendChild(li);
                }
            } catch (err) {
                console.error("[Smart Queue] queue fetch failed:", err);
            }
        }

        setInterval(refreshStatus, 3000);
        setInterval(refreshQueueList, 5000);
        await refreshStatus();
        await refreshQueueList();
    },
});
