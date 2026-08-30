import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDuration } from "../format_duration.js";

test("returns empty string for null", () => {
    assert.equal(formatDuration(null), "");
});

test("returns empty string for undefined", () => {
    assert.equal(formatDuration(undefined), "");
});

test("formats sub-minute durations as seconds", () => {
    assert.equal(formatDuration(45), "45s");
});

test("rounds fractional seconds", () => {
    assert.equal(formatDuration(45.6), "46s");
});

test("formats minute-plus durations as Xm Ys", () => {
    assert.equal(formatDuration(125), "2m 5s");
});

test("handles exactly one minute", () => {
    assert.equal(formatDuration(60), "1m 0s");
});
