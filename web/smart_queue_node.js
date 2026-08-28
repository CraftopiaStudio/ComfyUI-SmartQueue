import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SOUND_FILES = {
    Default: "sounds/default.wav",
    Chime: "sounds/chime.wav",
    Alert: "sounds/alert.wav",
};

app.registerExtension({
    name: "SmartQueue.CooldownNode",
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
            const { prompt_id } = event.detail;

            const overlay = document.createElement("div");
            overlay.className = "smart-queue-continue-overlay";
            overlay.innerHTML = `
                <div class="smart-queue-continue-box">
                    <div>Smart Cooldown & Pause is waiting — click Continue to resume.</div>
                    <button type="button" class="smart-queue-continue-btn">Continue</button>
                </div>
            `;
            document.body.appendChild(overlay);

            const cleanup = () => overlay.remove();

            overlay.querySelector(".smart-queue-continue-btn").addEventListener("click", async () => {
                try {
                    await fetch(`/smart_queue/continue/${encodeURIComponent(prompt_id)}`, { method: "POST" });
                } catch (err) {
                    console.error("[Smart Queue] continue request failed:", err);
                } finally {
                    cleanup();
                }
            });
        });
    },
});
