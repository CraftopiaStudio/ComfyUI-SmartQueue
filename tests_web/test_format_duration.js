import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDuration, formatEta } from "../web/format_duration.js";

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

test("eta is empty when the backend has no estimate", () => {
    assert.equal(formatEta(null), "");
    assert.equal(formatEta(undefined), "");
});

test("eta under a minute says so instead of ~0m", () => {
    assert.equal(formatEta(0), "<1m");
    assert.equal(formatEta(59), "<1m");
});

test("eta rounds to whole minutes, no false seconds precision", () => {
    assert.equal(formatEta(14 * 60 + 20), "~14m");
    assert.equal(formatEta(14 * 60 + 40), "~15m");
});

test("eta past an hour shows hours and padded minutes", () => {
    assert.equal(formatEta(3600 + 5 * 60), "~1h 05m");
    assert.equal(formatEta(3 * 3600), "~3h 00m");
});
