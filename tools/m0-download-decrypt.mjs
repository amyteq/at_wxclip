// M0 verification: take one captured media entry, download it directly (NOT through the
// proxy) and XOR the keystream over the head of the file, then check the result looks
// like a real mp4.
//
// The keystream comes from the reference implementation's Emscripten module, which is
// not vendored here (5.2 MB, and whether we keep it or port ISAAC64 to Python is an M1
// decision). Point --decrypt-js at it:
//
//   node tools/m0-download-decrypt.mjs --list
//   node tools/m0-download-decrypt.mjs --index 0 --out captures/m0/one.mp4 \
//        --decrypt-js ~/jedi/ateq/WeChatVideoDownloader/electron/decrypt.js
//
// decode_key must stay a string the whole way: it is a decimal integer that loses
// precision as a JS number, and the wasm signature is std::string.
import fs from "node:fs";
import path from "node:path";
import { readFileSync, readdirSync } from "node:fs";

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
  "Chrome/100.0.4896.127 Safari/537.36";

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : (process.argv[i + 1] ?? true);
}
const has = (name) => process.argv.includes(`--${name}`);

function newestCapturesFile() {
  const dir = "captures";
  const files = readdirSync(dir)
    .filter((f) => f.startsWith("captures-") && f.endsWith(".jsonl"))
    .map((f) => path.join(dir, f))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  if (!files.length) throw new Error("no captures/captures-*.jsonl found");
  return files[0];
}

/** Every media entry that carries a decode_key, flattened across capture events. */
function mediaEntries(file) {
  const out = [];
  for (const line of readFileSync(file, "utf8").split("\n")) {
    if (!line.trim()) continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    if ((row.event ?? row.kind) !== "capture") continue;
    for (const m of row.media ?? row.payload?.media ?? []) {
      if (m.decode_key && (m.download_url || m.url)) out.push(m);
    }
  }
  return out;
}

/* The Emscripten runtime initialises asynchronously, so the first call can throw. */
async function keystreamFor(decryptJsPath, key) {
  const mod = await import(path.resolve(decryptJsPath));
  const deadline = Date.now() + 20_000;
  for (;;) {
    try {
      const arr = mod.getDecryptionArray(key);
      if (arr?.length) return arr;
      throw new Error("empty keystream");
    } catch (err) {
      if (Date.now() > deadline) throw err;
      await new Promise((r) => setTimeout(r, 200));
    }
  }
}

const capturesFile = arg("captures") ?? newestCapturesFile();
const entries = mediaEntries(capturesFile);

if (has("list") || !entries.length) {
  console.log(`${entries.length} media entries with a decode_key in ${capturesFile}`);
  entries.forEach((m, i) => {
    console.log(
      `  [${i}] file_size=${m.file_size} decode_key=${String(m.decode_key).length} digits` +
        ` (${typeof m.decode_key}) host=${new URL(m.download_url ?? m.url).host}`,
    );
  });
  process.exit(entries.length ? 0 : 1);
}

const index = Number(arg("index", "0"));
const media = entries[index];
if (!media) throw new Error(`no media entry at index ${index}`);

const decryptJs = arg("decrypt-js") ?? process.env.WX_DLH_DECRYPT_JS;
if (!decryptJs) throw new Error("pass --decrypt-js <path to decrypt.js>");

const out = arg("out", `captures/m0/media-${index}.mp4`);
fs.mkdirSync(path.dirname(out), { recursive: true });

const key = String(media.decode_key);
console.log(`entry [${index}]: file_size=${media.file_size}, decode_key ${key.length} digits`);

const keystream = await keystreamFor(decryptJs, key);
console.log(`keystream: ${keystream.length} bytes`);

const url = media.download_url ?? `${media.url}${media.url_token ?? ""}`;
const res = await fetch(url, { headers: { "User-Agent": UA } });
if (!res.ok) throw new Error(`download failed: HTTP ${res.status}`);
const body = Buffer.from(await res.arrayBuffer());
console.log(`downloaded: ${body.length} bytes (content-length ${res.headers.get("content-length")})`);

const head = body.subarray(0, Math.min(keystream.length, body.length));
const before = Buffer.from(head.subarray(0, 16)).toString("hex");
for (let i = 0; i < head.length; i++) head[i] ^= keystream[i];
console.log(`first 16 bytes: ${before} -> ${Buffer.from(head.subarray(0, 16)).toString("hex")}`);
console.log(`box type at offset 4: ${body.subarray(4, 8).toString("latin1")}`);

fs.writeFileSync(out, body);
console.log(`wrote ${out}`);
