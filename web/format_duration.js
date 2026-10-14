export function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) return "";
    const total = Math.round(seconds);
    if (total < 60) return `${total}s`;
    return `${Math.floor(total / 60)}m ${total % 60}s`;
}

// The queue estimate is a median times a job count, so seconds would be false
// precision. Rounded minutes decide the unit, so 59m40s reads as ~1h 00m.
export function formatEta(seconds) {
    if (seconds === null || seconds === undefined) return "";
    if (seconds < 60) return "<1m";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `~${minutes}m`;
    return `~${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}
