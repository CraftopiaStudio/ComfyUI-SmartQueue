export function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) return "";
    const total = Math.round(seconds);
    if (total < 60) return `${total}s`;
    return `${Math.floor(total / 60)}m ${total % 60}s`;
}
