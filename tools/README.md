# tools

辅助小工具，不属于交付包，允许糙一点：抓包记录的整理、协议探测、一次性的数据转换等。

- `check-inject.mjs` —— 用 Node 在桩化的浏览器全局下跑 `web/inject.js`，重点验
  `decode_key` 64 位整数不丢精度（`node tools/check-inject.mjs`；
  `tests/test_inject_js.py` 在有 node 时会自动跑它）

一旦某个工具变成日常流程的一部分，就搬进 `wxclip/` 或 `scripts/` 并补测试。
