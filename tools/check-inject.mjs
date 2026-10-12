/* Drive web/inject.js under Node with stubbed browser globals.
 *
 * The point of this check is the precision guard: a JSAPI response carries decode_key as
 * a bare 64-bit number, and anything that reaches it through JSON.parse gets a float.
 * Run it with `node tools/check-inject.mjs`; tests/test_inject_js.py does the same when
 * node is on PATH.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const CAPTURE_PATH = "/__wx-dlh/capture";
const ORIGIN = "https://channels.weixin.qq.com";

const posts = [];

globalThis.window = globalThis;
globalThis.location = { href: `${ORIGIN}/web/pages/feed`, origin: ORIGIN };
// node >= 21 ships a read-only navigator, so it has to be redefined rather than assigned
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    userAgent: "Mozilla/5.0 MicroMessenger/7.0.20.1781 UnifiedPCLinuxWechat",
    sendBeacon: () => true,
  },
});
globalThis.document = { readyState: "complete", referrer: "", addEventListener() {} };
globalThis.fetch = (url, init) => {
  posts.push({ url, body: JSON.parse(init.body) });
  return Promise.resolve({ ok: true });
};
globalThis.Blob = class Blob {};

const source = readFileSync(join(root, "web", "inject.js"), "utf8")
  .replaceAll("__WX_DLH_CAPTURE_PATH__", CAPTURE_PATH)
  .replaceAll("__WX_DLH_VERSION__", "test")
  .replaceAll('"__WX_DLH_INCLUDE_SNIPPET__"', "true");
assert.ok(!source.includes("__WX_DLH_"), "template placeholders left in the script");
vm.runInThisContext(source, { filename: "web/inject.js" });

// 1. the script announces itself, and says whether a bridge was there yet
const probe = posts.at(-1);
assert.equal(probe.url, ORIGIN + CAPTURE_PATH);
assert.equal(probe.body.kind, "probe");
assert.equal(probe.body.has_bridge, false);
assert.equal(probe.body.page_url, `${ORIGIN}/web/pages/feed`);
console.log("ok  probe reported, capture url", probe.url);

// 2. a bridge installed after us is still hooked, because we own the property
const KEY = "13835058055282163712";
const respJson =
  '{"object":{"object_desc":{"media":[{"url":"https://finder.video.qq.com/251/x",' +
  `"url_token":"&token=1","decode_key":${KEY},"file_size":4194304,` +
  '"description":"a clip","nickname":"someone"}]}}}';

assert.notEqual(
  String(JSON.parse(respJson).object.object_desc.media[0].decode_key),
  KEY,
  "this response was supposed to be one JSON.parse mangles",
);
console.log(
  "ok  JSON.parse would have turned the key into",
  String(JSON.parse(respJson).object.object_desc.media[0].decode_key),
);

let calledBack = 0;
window.WeixinJSBridge = {
  invoke(api, params, callback) {
    callback({ err_msg: "finderH5ExtTransfer:ok", jsapi_resp: { resp_json: respJson } });
  },
};
assert.equal(window.__wx_dlh.hooked, true, "bridge assignment was not hooked");
assert.equal(posts.at(-1).body.kind, "hook");

window.WeixinJSBridge.invoke("getVideoInfo", {}, () => {
  calledBack++;
});
assert.equal(calledBack, 1, "the page's own callback must still run");

const capture = posts.at(-1).body;
assert.equal(capture.kind, "capture");
assert.equal(capture.err_msg, "finderH5ExtTransfer:ok");
assert.equal(capture.api, "getVideoInfo");
assert.equal(capture.media.length, 1);
assert.equal(capture.media[0].decode_key, KEY, "decode_key lost its low digits");
assert.equal(capture.media[0].decode_key_json_type, "number");
assert.equal(capture.media[0].url, "https://finder.video.qq.com/251/x");
assert.equal(capture.media[0].url_token, "&token=1");
assert.equal(capture.media[0].file_size, 4194304);
console.log("ok  captured decode_key verbatim:", capture.media[0].decode_key);

// 3. several media entries keep their own keys, in order
const two = `{"media":[{"url":"a","decode_key":"${KEY}"},{"url":"b","decode_key":1}]}`;
window.WeixinJSBridge = {
  invoke: (api, params, callback) => callback({ err_msg: "ok", jsapi_resp: { resp_json: two } }),
};
window.WeixinJSBridge.invoke("getVideoInfo", {}, () => {});
const pair = posts.at(-1).body.media;
assert.deepEqual(
  pair.map((m) => [m.url, m.decode_key, m.decode_key_json_type]),
  [
    ["a", KEY, "string"],
    ["b", "1", "number"],
  ],
);
console.log("ok  media entries keep their own keys and quoting");

// 4. an invoke without media reports its shape only - no values leave the page
window.WeixinJSBridge = {
  invoke: (api, params, callback) =>
    callback({ err_msg: "getUserInfo:ok", nickname: "someone", openid: "o_123" }),
};
window.WeixinJSBridge.invoke("getUserInfo", {}, () => {});
const diagnostic = posts.at(-1).body;
assert.equal(diagnostic.kind, "invoke");
assert.equal(diagnostic.err_msg, "getUserInfo:ok");
assert.deepEqual(diagnostic.res_keys, ["err_msg", "nickname", "openid"]);
assert.ok(!JSON.stringify(diagnostic).includes("o_123"), "a value leaked out of the page");
console.log("ok  non-media invoke reported keys only:", diagnostic.res_keys.join(", "));

// 5. handing us the same bridge again does nothing
const before = posts.length;
window.WeixinJSBridge = window.WeixinJSBridge;
assert.equal(posts.length, before, "re-hooked an already hooked bridge");
console.log("ok  hooking is idempotent");

console.log(`\nall checks passed (${posts.length} posts, ${window.__wx_dlh.invokes} invokes seen)`);
