# tools

辅助小工具，不属于交付包，允许糙一点：抓包记录的整理、协议探测、一次性的数据转换等。

- `check-inject.mjs` —— 用 Node 在桩化的浏览器全局下跑 `web/inject.js`，重点验
  `decode_key` 64 位整数不丢精度（`node tools/check-inject.mjs`；
  `tests/test_inject_js.py` 在有 node 时会自动跑它）
- `m0-download-decrypt.mjs` —— 从 `captures/captures-*.jsonl` 里挑一条 `media`，直连下载
  （**不走代理**）并 XOR 掉前 128 KiB，用来实证解密链路。keystream 模块不在本仓库里，
  用 `--decrypt-js` 指到参考实现的 `electron/decrypt.js`：
  `node tools/m0-download-decrypt.mjs --list`、
  `node tools/m0-download-decrypt.mjs --index 0 --out captures/m0/one.mp4 --decrypt-js <path>`。
  M1 会被 `wxclip download` / `wxclip decrypt` 取代

一旦某个工具变成日常流程的一部分，就搬进 `wxclip/` 或 `scripts/` 并补测试。
