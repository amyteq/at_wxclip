# at_wxclip

把 Linux 版 WeChat 里能播放的视频号视频抓下来、解密成可播放文件，再做截屏、文字提取和
总结。一个 headless 命令行工具 + 一个轻量 web server 作为 GUI。

## 工作原理

机制以参考实现 [`WeChatVideoDownloader`](https://github.com/harrysun2006/WeChatVideoDownloader)
的代码为准（已逐行核对，见 `docs/项目背景.md`）：

```text
① WeChat 打开视频号页面
        │
        ▼
② wxclip MITM 代理（必须安装并信任本地 CA，否则看不到 HTTPS 内容）
        ├── 改写 channels.weixin.qq.com 的 HTML 响应，在 </body> 前注入一个 <script>
        └── 脚本走页面同源 /__wxclip/inject.js，由代理自己应答
            （参考实现是顶替 res.wx.qq.com 上某个脚本的响应体）
        │
        ▼
③ 注入脚本 hook window.WeixinJSBridge.invoke，从 JSAPI 响应里取出每条
   media[] 的 url + url_token、decode_key、file_size、描述、作者
   （实测一次 capture 带 8 条，不是一条；参考实现只取 media[0]）
        │
        ▼
④ 回传给 wxclip：POST 同源的 /__wxclip/capture，代理拦下，不转发上游
   （参考实现用哨兵域名 aaaa.com 做这件事）
        │
        ▼
⑤ 直连下载 url（不走代理）
        │
        ▼
⑥ 以 decode_key 为种子生成 ISAAC64 keystream（131072 字节 = 128 KiB），
   XOR 掉文件的前 128 KiB，其余字节原样保留 → 可播放的 mp4
        │
        ▼
⑦ 截屏 / 文字提取 / 内容总结
```

### 三个容易搞错的地方

1. **decoder 不是「解析真实下载地址」，而是解密已下载文件的头部。** 下载地址本身不需要
   解析，它就是 JSAPI 响应里的 `url + url_token`；`decrypt.js` 只负责用 `decode_key`
   生成 keystream。项目介绍讨论里「再经过 decoder 来解析出真实的下载地址」这句话与参考
   实现的代码不一致，以代码为准。
2. **只被动抓 URL 是不够的。** `decode_key` 不出现在任何网络请求里，只出现在
   `WeixinJSBridge` 的 JSAPI 响应中，所以参考实现要注入脚本去 hook 它。拿到 URL 却没有
   `decode_key`，下载到的文件前 128 KiB 是密文，播不了。
3. **`decode_key` 全程当字符串传。** 它是 64 位整数，wasm 侧签名是 `std::string`；一旦
   被当成 JS number 解析就会丢精度，keystream 完全不同而且不会报错。实测
   `13835058055282163712` 与其 number 化后的 `13835058055282164000` 生成的 keystream
   毫无关系。

## 技术栈

- **Python**：MITM 代理（注入 + 抓取 `decode_key`）、keystream、直连下载、XOR 解密、
  以及提供管理与下载面板的 web server。运行时**零第三方依赖**（代理那一半用可选的
  `mitmproxy`，下载走标准库 `urllib`）
- **JavaScript**：注入到视频号页面里的 hook 脚本 `web/inject.js`
- **假设 WeChat 运行在同一台 Linux 机器上** —— 注意参考实现只支持 Win/Mac（代理设置走
  Windows 注册表或 macOS `networksetup`，没有 Linux 分支），Linux 侧是自己打通的，见
  `docs/项目背景.md`

**keystream 不再需要 Node。** 参考实现把它交给 `decrypt.js`（5.2 MB 的 Emscripten 产物，
wasm 以 base64 内嵌，导出 `getDecryptionArray(seedString)`）；M1 把它认出来是标准
ISAAC64，用 `decode_key` 作种子填进结果数组第 0 槽，每个 64 位字按**大端**吐出 131072
字节。`wxclip/keystream.py` 就是这个算法的 Python 实现，和 `decrypt.js` 的输出**逐字节
一致**——`evals/README.md` 的黄金样本表由 `decrypt.js` 生成，`tests/test_keystream.py`
每次都比对，`tools/keystream-golden.mjs` 可以重新生成这张表并顺手校验移植版。

## 快速开始

```bash
make setup          # 创建 .venv 并安装依赖（uv，回落到 pip；含代理用的 mitmproxy）
make lint test      # 检查 + 测试
python -m wxclip --help
```

准备工作两步，细节和判定方法见 `docs/M0-操作步骤.md`：

```bash
.venv/bin/python -m wxclip ca      # 生成本地 CA，打印指纹与该装到哪个信任库
scripts/trust-ca.sh                # 装进探测到的信任库（本机 WeChat 是 Flatpak → 沙箱内 NSS）
```

然后一条命令从播放到可播放文件（默认抓到第一个就停，文件落在 `downloads/`）：

```bash
.venv/bin/python -m wxclip capture           # 起代理 → 注入 → 抓 key → 下载 → 解密
.venv/bin/python -m wxclip capture --count 3 --max-size 200m --wait 120
```

拆开来用也行：

```bash
wxclip proxy                                 # 只抓，不下载；事件写进 captures/
wxclip download --list                       # 看最近一次抓到了哪些 media
wxclip download --from-capture --index 2     # 按序号直连下载（不走代理）+ 解密
wxclip download <url> --key <decode_key>     # 手工给地址和 key
wxclip decrypt <file> --key <decode_key>     # 只对已下载的密文重跑解密
```

下载是流式的、带进度、可断点续传：中断会留下 `.part`，再跑一次接着下（`.part` 里存的已经是
解密后的字节，续传时 keystream 会从该偏移接上）。`file_size` 实测跨度 3.8 MB – 585 MB，
所以任何时候都不会把整个文件读进内存。

代理运行时的产物都在 `captures/`（git 忽略）：`traffic-*.jsonl` 每条 flow 的摘要、
`captures-*.jsonl` 注入脚本回传的事件（含 `decode_key`）、`pages/*.html` 注入前的原始页面、
`flows-*.mitm` 完整 flow 存档（`mitmproxy -r` 回看）。

同源的 `/__wxclip/*` 比参考实现的 `res.wx.qq.com` + 哨兵域名 `aaaa.com` 少一层 CSP 与跨域
风险，代价是页面必须是 HTML 响应。M0 已经确认 Linux 客户端走的就是
`channels.weixin.qq.com` 的 H5 页面，注入路线成立。

## 目录

| 路径       | 说明                                                    |
|------------|---------------------------------------------------------|
| `wxclip/`  | Python 包：proxy / capture / keystream / decrypt / downloader / pipeline |
| `web/`     | 注入脚本与前端页面                                       |
| `docs/`    | 背景、开发计划、规格说明                                 |
| `tests/`   | pytest 测试                                              |
| `scripts/` | 给人手动执行的入口脚本                                   |
| `tools/`   | 辅助小工具，不属于交付包                                 |
| `evals/`   | 解码/提取/总结质量的固定样本与校验                        |

详见 `docs/目录说明.md`；智能体协作约定见 `AGENTS.md`。

## 参考

- 参考实现：https://github.com/harrysun2006/WeChatVideoDownloader
  （上游 lecepin/WeChatVideoDownloader，v2.x 起支持加密视频）
- keystream 模块（本项目已移植进 Python，这里留作黄金样本的来源）：
  [`electron/decrypt.js`](https://github.com/harrysun2006/WeChatVideoDownloader/blob/master/electron/decrypt.js)
- 项目介绍讨论：https://github.com/amyteq/at_wxclip/discussions/2
- 开发计划 issue：M0 https://github.com/amyteq/at_wxclip/issues/4 、
  M1 https://github.com/amyteq/at_wxclip/issues/8

## 说明

仅用于下载本人 WeChat 客户端中已可正常播放的内容，供个人使用。不做付费/受限内容的
DRM 破解，不做他人账号的批量抓取，不再分发抓取到的媒体文件。
