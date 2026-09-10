# web

前端页面 + 解析视频真实地址的 JS 逻辑。两件事：

1. **decoder** —— 参考
   [`decrypt.js`](https://github.com/harrysun2006/WeChatVideoDownloader/blob/master/electron/decrypt.js)，
   目标是能在纯 Node 环境（不依赖 Electron）里被 Python 侧当子进程调用；
2. **dashboard** —— 由 `wxclip serve` 提供的管理与下载面板，保持朴素。

构建工具与是否上 TypeScript 等到 M1/M2 再定（见 `docs/开发计划.md`），现在不预先铺
`package.json` 和依赖。构建产物（`dist/`、`node_modules/`）不进 Git。
