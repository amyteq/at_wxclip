# web

两件事，都是 JavaScript，注意它们的职责和讨论里的描述不一样（见 `docs/项目背景.md`）：

1. **注入脚本（hook script）** —— `inject.js`，由代理注入到视频号 H5 页面里，hook
   `window.WeixinJSBridge.invoke`，从 JSAPI 响应里取出 `media[0]` 的
   `url + url_token`、`decode_key`、`file_size`，回传给 wxclip。
   **`decode_key` 只在这里拿得到**，被动抓请求是抓不到的。
   参考实现见 `electron/proxyServer.js` 里的 `injection_script`。

   实现上和参考实现有意不同的三点：脚本与回传都走**页面同源**的 `/__wxclip/*`（代理自己应答）；
   `decode_key` 用正则从响应**原文**里抠出来，绝不经过 `JSON.parse`（裸数字会静默变 float）；
   不靠 `err_msg` 筛选媒体响应（Linux 上那个字符串未知），而是记录每一次 `invoke` 的
   `err_msg`，靠响应里有没有 `decode_key` 判断。

   `__WXCLIP_CAPTURE_PATH__` / `__WXCLIP_VERSION__` / `__WXCLIP_INCLUDE_SNIPPET__` 三个占位符
   由 `wxclip.inject.hook_script()` 在服务时替换。改这个文件前后跑
   `node tools/check-inject.mjs`（`make test` 在有 node 时会带上它）。

2. **keystream 模块** ——
   [`decrypt.js`](https://github.com/harrysun2006/WeChatVideoDownloader/blob/master/electron/decrypt.js)，
   5.2 MB 的 Emscripten 产物（wasm 以 base64 内嵌），导出
   `getDecryptionArray(seedString)` → 131072 字节 ISAAC64 keystream，用来 XOR 掉下载
   文件的前 128 KiB。它**不解析下载地址**。
   已实测可在纯 Node v24 里脱离 Electron 运行，注意两点：运行时是异步初始化的，import
   后要等就绪；seed 必须传字符串（wasm 侧是 `std::string`）。

   要不要把它换成 Python 版 ISAAC64（省掉 Node 依赖），M1 再定；换的时候用它的输出做
   黄金样本。

3. **dashboard** —— 由 `wxclip serve` 提供的管理与下载面板，保持朴素。

构建工具与是否上 TypeScript 等到 M1/M2 再定（见 `docs/开发计划.md`），现在不预先铺
`package.json` 和依赖。构建产物（`dist/`、`node_modules/`）不进 Git。
