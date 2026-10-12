# tools

辅助小工具，不属于交付包，允许糙一点：抓包记录的整理、协议探测、一次性的数据转换等。

- `check-inject.mjs` —— 用 Node 在桩化的浏览器全局下跑 `web/inject.js`，重点验 `decode_key` 64 位整数不丢精度（`node tools/check-inject.mjs`；`tests/test_inject_js.py` 在有 node 时会自动跑它）
- `keystream-golden.mjs` —— 用参考实现的 `decrypt.js` 生成 `evals/README.md` 里那张 keystream 黄金样本表，并顺手拿同一个种子调一次 `wx_dlh.keystream` 做比对（不一致就非零退出）。改动 `wx_dlh/keystream.py` 之后跑它：`node tools/keystream-golden.mjs --decrypt-js <path>/electron/decrypt.js`，加新种子用 `--seed <十进制字符串>`
- `m0-download-decrypt.mjs` —— 从 `captures/captures-*.jsonl` 里挑一条 `media`，直连下载（**不走代理**）并 XOR 掉前 128 KiB。日常用途已由 `wx-dlh download --from-capture` 取代（而且那个是流式的，这个整块读进内存，585 MB 的样本会把机器吃掉）；留着是因为它完全不经过 Python，是一条独立的验证路径。keystream 模块不在本仓库里，用 `--decrypt-js` 指到参考实现的 `electron/decrypt.js`：`node tools/m0-download-decrypt.mjs --list`、`node tools/m0-download-decrypt.mjs --index 0 --out captures/m0/one.mp4 --decrypt-js <path>`

一旦某个工具变成日常流程的一部分，就搬进 `wx_dlh/` 或 `scripts/` 并补测试。
