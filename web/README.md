# web

两件事：注入脚本，和 M2 起的 dashboard（keystream 自 M1 起在 Python 侧）。注意这里的
职责和讨论里的描述不一样，见 `docs/项目背景.md`：

1. **注入脚本（hook script）** —— `inject.js`，由代理注入到视频号 H5 页面里，hook
   `window.WeixinJSBridge.invoke`，从 JSAPI 响应里取出**每一条** `media[]` 的
   `url + url_token`、`decode_key`、`file_size`，回传给 wxclip（实测一次 8 条；参考实现
   只取 `media[0]`，选哪一条由 `wxclip capture --index/--all` 决定）。
   **`decode_key` 只在这里拿得到**，被动抓请求是抓不到的。
   参考实现见 `electron/proxyServer.js` 里的 `injection_script`。

   实现上和参考实现有意不同的三点：脚本与回传都走**页面同源**的 `/__wxclip/*`（代理自己应答）；
   `decode_key` 用正则从响应**原文**里抠出来，绝不经过 `JSON.parse`（裸数字会静默变 float）；
   不靠 `err_msg` 筛选媒体响应，而是记录每一次 `invoke` 的 `err_msg`，靠响应里有没有
   `decode_key` 判断 —— M0 之后已经知道 Linux 上是 `finderH5ExtTransfer` +
   `H5ExtTransfer:ok`（见 `evals/jsapi-response-linux-20260911.md`），但这条判据不改：
   它不依赖任何一个客户端的字符串。

   `__WXCLIP_CAPTURE_PATH__` / `__WXCLIP_VERSION__` / `__WXCLIP_INCLUDE_SNIPPET__` 三个占位符
   由 `wxclip.inject.hook_script()` 在服务时替换。改这个文件前后跑
   `node tools/check-inject.mjs`（`make test` 在有 node 时会带上它）。

2. ~~**keystream 模块**~~ —— **M1 已经移到 Python**，`web/` 这边不再有它的位置。
   参考实现的
   [`decrypt.js`](https://github.com/harrysun2006/WeChatVideoDownloader/blob/master/electron/decrypt.js)
   （5.2 MB Emscripten 产物，导出 `getDecryptionArray(seedString)` → 131072 字节
   ISAAC64 keystream）里的算法就是标准 ISAAC64，已实现为 `wxclip/keystream.py`，输出
   逐字节一致。它现在只剩一个角色：`evals/README.md` 黄金样本表的来源，用
   `tools/keystream-golden.mjs` 生成与比对。

3. **dashboard** —— `dashboard.html` + `dashboard.js`，由 `wxclip serve` 提供的管理与下载面板
   （`wxclip/server.py`）。原生 DOM，一个页面加一个脚本，没有框架、没有 CDN、没有构建步骤：
   它从 localhost 发出来、列的是隔壁目录里的文件，断网也该照样能用。

   两条不能破的规矩：**`decode_key` 永远是文本**（64 位整数，`Number()` 或任何算术都会静默
   丢精度，页面里用不到它的值）；**从 WeChat 页面来的文本一律走 `textContent` / `createElement`，
   不碰 `innerHTML`**（说明、昵称、文件名都属于此列）。

   页面只跟三个端点打交道：`GET /api/state`（轮询，忙时 1 s、闲时 3 s）、
   `POST /api/download`（只提交它刚展示过的那条记录的裸 `url`）、`GET /files/<name>`
   （播放或另存，支持 Range）。

构建工具与是否上 TypeScript：M2 的结论是都不要 —— 面板这个体量用原生 DOM 更省事，所以
`package.json` 和依赖仍然没有铺。真需要了再说。构建产物（`dist/`、`node_modules/`）不进 Git。
