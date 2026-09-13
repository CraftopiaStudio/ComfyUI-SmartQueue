/**
 * Guards how the panel addresses its backend.
 *
 * ComfyUI serves every route twice: the bare path and an /api-prefixed copy.
 * A setup that reaches ComfyUI through a reverse proxy or the frontend dev
 * server commonly forwards only /api, and a subpath deployment moves the whole
 * app off /. A root-relative fetch("/smart_queue/...") misses both, so the
 * panel silently cannot reach its own backend while everything looks fine on a
 * plain localhost install.
 *
 * api.fetchApi()/api.apiURL() apply the frontend's own api_base and the /api
 * prefix, so routing every backend call through them is what makes those
 * setups work. This is a source-level check rather than a behavioural one for
 * the same reason tests/test_cooldown_schema_order.py parses source: the panel
 * needs a live ComfyUI to run, and the rule being pinned is a convention that
 * is easy to break by writing one more plain fetch.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const MODULES = ["smart_queue.js", "smart_queue_node.js"];

function source(name) {
    return readFileSync(fileURLToPath(new URL(`../web/${name}`, import.meta.url)), "utf8");
}

test("no panel module calls bare fetch()", () => {
    for (const name of MODULES) {
        const bare = [...source(name).matchAll(/(\w+\.)?\bfetch\(/g)].filter(
            (m) => m[1] !== "api.",
        );
        assert.deepEqual(
            bare.map((m) => m[0]),
            [],
            `${name} calls fetch() directly; use api.fetchApi() so the /api copy and api_base apply`,
        );
    }
});

test("no panel module points a DOM src/href at a root-relative URL", () => {
    // Thumbnails load from ComfyUI's own /view endpoint. Assigning it raw
    // resolves against the site root, which breaks under a subpath deployment
    // exactly like a bare fetch does; api.apiURL() is the src-side equivalent
    // of fetchApi. Stylesheet hrefs built from import.meta.url are already
    // module-relative and are deliberately not matched here.
    for (const name of MODULES) {
        const rooted = [...source(name).matchAll(/\.(?:src|href)\s*=\s*["'`](\/[^"'`]*)/g)];
        assert.deepEqual(
            rooted.map((m) => m[1]),
            [],
            `${name} assigns a root-relative URL to src/href; wrap it in api.apiURL()`,
        );
    }
});

test("every panel module imports the api object", () => {
    for (const name of MODULES) {
        const importsApi = /import \{[^}]*\bapi\b[^}]*\} from "\.\.\/\.\.\/scripts\/api\.js";/.test(
            source(name),
        );
        assert.equal(importsApi, true, `${name} does not import api from scripts/api.js`);
    }
});
