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
③ 注入脚本 hook window.WeixinJSBridge.invoke，从 JSAPI 响应里取出
   media[0] 的 url + url_token、decode_key、file_size、描述、作者
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

- **Python**：MITM 代理（注入 + 抓取 `decode_key`）、直连下载、XOR 解密、以及提供管理与
  下载面板的 web server
- **JavaScript**：注入到视频号页面里的 hook 脚本；以及 keystream 模块
  `decrypt.js` —— 它是 5.2 MB 的 Emscripten 产物（wasm 以 base64 内嵌），导出
  `getDecryptionArray(seedString)`，返回 131072 字节 keystream
- **假设 WeChat 运行在同一台 Linux 机器上** —— 注意参考实现只支持 Win/Mac（代理设置走
  Windows 注册表或 macOS `networksetup`，没有 Linux 分支），Linux 侧要自己打通，这是
  M0 spike 的首要任务

`decrypt.js` 已实测可以在**纯 Node（v24）里脱离 Electron 运行**，只需等它的运行时异步
初始化完成；也可以把 ISAAC64 移植进 Python 省掉 Node 依赖，细节见 `docs/开发计划.md`。

## 快速开始

```bash
make setup          # 创建 .venv 并安装依赖（uv，回落到 pip；含代理用的 mitmproxy）
make lint test      # 检查 + 测试
python -m wxclip --help
```

抓取用两步，细节和判定方法见 `docs/M0-操作步骤.md`：

```bash
.venv/bin/python -m wxclip ca      # 生成本地 CA，打印指纹与该装到哪个信任库
scripts/trust-ca.sh                # 装进探测到的信任库（本机 WeChat 是 Flatpak → 沙箱内 NSS）
.venv/bin/python -m wxclip proxy   # 默认 127.0.0.1:8899，在 WeChat 里设为 HTTP 代理后重启它
```

代理运行时的产物都在 `captures/`（git 忽略）：`traffic-*.jsonl` 每条 flow 的摘要、
`captures-*.jsonl` 注入脚本回传的事件（含 `decode_key`）、`pages/*.html` 注入前的原始页面、
`flows-*.mitm` 完整 flow 存档（`mitmproxy -r` 回看）。

同源的 `/__wxclip/*` 比参考实现的 `res.wx.qq.com` + 哨兵域名 `aaaa.com` 少一层 CSP 与跨域
风险，代价是页面必须是 HTML 响应（原生渲染的页面注入不进去，这正是 M0 要判定的事）。

## 目录

| 路径       | 说明                                                    |
|------------|---------------------------------------------------------|
| `wxclip/`  | Python 包：proxy / keystream 桥接 / downloader / server |
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
- keystream 模块：
  [`electron/decrypt.js`](https://github.com/harrysun2006/WeChatVideoDownloader/blob/master/electron/decrypt.js)
- 项目介绍讨论：https://github.com/amyteq/at_wxclip/discussions/2
- 开发计划 issue：https://github.com/amyteq/at_wxclip/issues/4

## 说明

仅用于下载本人 WeChat 客户端中已可正常播放的内容，供个人使用。不做付费/受限内容的
DRM 破解，不做他人账号的批量抓取，不再分发抓取到的媒体文件。
