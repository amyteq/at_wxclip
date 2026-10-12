/* Regenerate the keystream golden table in evals/README.md from the reference
 * implementation, and check the Python port against it.
 *
 * `wx_dlh.keystream` is a from-scratch ISAAC64 in Python; the only thing that makes it
 * trustworthy is that it produces byte-for-byte what `electron/decrypt.js` produces. That
 * file is 5.2 MB of Emscripten output and is not vendored here, so point --decrypt-js at
 * a checkout of harrysun2006/WeChatVideoDownloader:
 *
 *   node tools/keystream-golden.mjs --decrypt-js ~/…/WeChatVideoDownloader/electron/decrypt.js
 *   node tools/keystream-golden.mjs --seed 4145908544 --decrypt-js <path>
 *
 * It prints a markdown row per seed and, when .venv is present, compares each one with
 * `wx_dlh.keystream.keystream()` - which is the check that matters.
 */
import crypto from "node:crypto";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

/* The seeds evals/README.md pins. The last two are deliberate: 13835058055282163712 is
 * the value JavaScript prints back as 13835058055282164000, and 18446744073709551615 is
 * the uint64 ceiling. 1234567890 covers the 10-digit length Linux actually sends. */
const DEFAULT_SEEDS = ["0", "1", "1234567890", "13835058055282163712", "18446744073709551615"];
const KEYSTREAM_BYTES = 131072;

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : (process.argv[i + 1] ?? true);
}

const decryptJs = arg("decrypt-js") ?? process.env.WX_DLH_DECRYPT_JS;
if (!decryptJs) throw new Error("pass --decrypt-js <path to the reference decrypt.js>");
const seeds = arg("seed") ? [String(arg("seed"))] : DEFAULT_SEEDS;

const mod = await import(path.resolve(decryptJs));

/* The Emscripten runtime initialises asynchronously, so the first call can throw. */
async function keystreamFor(seed) {
  const deadline = Date.now() + 20_000;
  for (;;) {
    try {
      const arr = mod.getDecryptionArray(seed); // a string: the wasm side is std::string
      if (arr?.length) return Buffer.from(arr);
      throw new Error("empty keystream");
    } catch (err) {
      if (Date.now() > deadline) throw err;
      await new Promise((r) => setTimeout(r, 200));
    }
  }
}

const python = existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3";

function pythonDigest(seed) {
  try {
    return execFileSync(
      python,
      [
        "-c",
        "import hashlib,sys;from wx_dlh.keystream import keystream;" +
          "print(hashlib.sha256(keystream(sys.argv[1])).hexdigest())",
        seed,
      ],
      { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
    ).trim();
  } catch {
    return null; // no venv, or wx-dlh not importable - the table is still worth printing
  }
}

console.log("| seed（字符串） | 长度 | keystream 的 sha256 |");
console.log("|----------------|------|---------------------|");
let mismatches = 0;
for (const seed of seeds) {
  const bytes = await keystreamFor(seed);
  const digest = crypto.createHash("sha256").update(bytes).digest("hex");
  if (bytes.length !== KEYSTREAM_BYTES) throw new Error(`${seed}: got ${bytes.length} bytes`);
  console.log(`| \`${seed}\` | ${bytes.length} | \`${digest}\` |`);
  const ours = pythonDigest(seed);
  if (ours && ours !== digest) {
    mismatches++;
    console.error(`  !! wx_dlh.keystream disagrees for ${seed}: ${ours}`);
  }
}
console.error(mismatches ? `${mismatches} seed(s) differ` : "wx_dlh.keystream matches decrypt.js");
process.exit(mismatches ? 1 : 0);
