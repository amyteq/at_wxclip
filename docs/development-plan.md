# 开发计划

依据 `docs/project-background.md`（讨论 #2）整理，并按参考实现的源码核对过一遍（2026-09-11，见该文件的「与参考实现核对后的更正」）。本文件是计划的事实源；里程碑勾选在 issue 里维护。

## Scope 评估

**中小型（M），但上限取决于一个前提**：如果 Linux 版 WeChat 的视频号仍然走 `channels.weixin.qq.com` 那个 webview H5 页面，那么 2–3 周零散投入可以拿到「能用」的版本 —— 参考实现已经把机制走通了，我们只是换个语言 + 补上 Linux 的代理与 CA 部分。

如果 Linux 客户端是原生渲染视频号，参考实现的注入路线**整条失效**，`decode_key` 得另找来源，那就不是 M 而是一个逆向项目，需要重新评估要不要做。

代码本身没有难点，风险集中在两处未知：

1. **Linux 版 WeChat 能不能被 MITM** —— 代理怎么设、CA 怎么让它信任。参考实现只有 Windows（注册表）和 macOS（`networksetup`）两条路，Linux 没有代码可借。
2. **Linux 客户端的视频号是不是同一条 H5 路径** —— 有没有可注入的页面、有没有 `window.WeixinJSBridge` 可以 hook。这是决定项目性质的那个问题。

### 已经不再是未知的部分

- **`decrypt.js` 可以脱离 Electron 复用** —— 已实测：纯 Node v24 里 import 即用，wasm 以 base64 内嵌在文件里，无需 Electron、无需额外 .wasm 文件。（M1 之后连这一步也不需要了：算法已移植进 `wx_dlh/keystream.py`，见下面 M1 那节。）
- **它到底做什么** —— ISAAC64 keystream 生成器：`getDecryptionArray(seedString)` 返回 131072 字节，用来 XOR 掉下载文件的**前 128 KiB**。它不解析下载地址；地址直接来自 JSAPI 响应的 `url + url_token`。
- **两个坑** —— seed 必须以字符串传（wasm 侧是 `std::string`，传 number 直接抛错）；`decode_key` 是 64 位整数，被当成 JS number 解析会静默丢精度、keystream 完全错位。

## 是否需要 specs：先不写，先做 spike

**建议现在不写完整 specs**，先用 1–2 天做 M0 spike 把上面两处未知打掉。spike 结束后：

- 两处都通了 → 直接按里程碑推进，本计划 + `docs/project-background.md` 里的协议记录当设计文档就够了；
- Linux 客户端不走 H5 / 没有 bridge → 停下来重新评估，这时才值得为「怎么拿到 `decode_key`」单独写一份 `docs/specs/decode-key.md`（需求 + 设计 + 可行性）。

理由：这个项目的成本不在写代码，而在摸清 Linux 客户端的行为；在摸清之前写出来的设计大概率要重写。参考实现的机制已经读明白了，不需要再为它写规格。

## 里程碑

### M0 — spike：打通 Linux 侧可行性（最先做，1–2 天）

工具侧已经就绪（`wx-dlh proxy` / `wx-dlh ca` / `web/inject.js`，操作步骤见 `docs/M0-runbook.md`），剩下的都是要真人在 WeChat 里操作才能验的：

- [x] 让 Linux 版 WeChat 走本机代理：**应用内代理设置可用**（issue #4 的截图）
- [x] 代理与 CA 落地：mitmproxy + 自签 CA（`~/.config/wx-dlh/ca/`），注入与回传通道已在真实 TLS 上验证（`channels.weixin.qq.com` 能被 MITM 出明文；注入与 `/__wx-dlh/*` 端点用 curl 走通）
- [x] 让 WeChat 信任这张 CA：**只有主进程做得到**。装进 Flatpak 沙箱的 NSS 库后主进程接受本地证书、HTTPS 解出明文（2026-09-11 实测，无一次 TLS 失败）；**webview 靠装 CA 永远不行**，它的根证书编译在 `WeChatAppEx` 二进制里，做校验的进程从不打开 NSS 数据库（证据见 `docs/project-background.md`）
- [x] **H5 路线是否存在**：成立。webview 打开的就是 `channels.weixin.qq.com/web/pages/feed?oid=…&nid=…`（标题「视频号」），URL 与参考实现一致
- [x] **把 webview 也塞进代理**：`flatpak override --env=http_proxy=…` 可行，webview 确实来连代理（桌面图标/托盘启动走 systemd app scope，`flatpak run --env=…` 对它无效）
- [x] **让 webview 接受我们的证书**：唯一的路是给它加 `--ignore-certificate-errors-spki-list=<base64(sha256(SPKI))>`（只放行我们这一个公钥）。它的命令行由 `libwmpf_host_export.so` 拼、不读环境变量，所以用 LD_PRELOAD 在 exec 时插进去（`scripts/appex-flags-preload.c` + `scripts/build-appex-preload.sh`）。**已生效**：switch 进了 browser 与 NetworkService 两个进程的 cmdline，页面正常打开
- [x] 页面经过代理后，确认注入生效、`window.WeixinJSBridge` 存在且可 hook：`probe` 里 `has_bridge: true`/`hooked: true`，`hook` 里 `has_invoke: true`/`has_on: true`
- [x] 抓一次真实的 JSAPI 响应：api 是 `finderH5ExtTransfer`（同 macOS）、`err_msg` 是 `H5ExtTransfer:ok`（同 Windows），`decode_key` 是**带引号的字符串**、8–10 位十进制（不是 20 位）。脱敏样本进了 `evals/jsapi-response-linux-20260911.md`
- [x] 用 Node 跑 `decrypt.js` + 真实 `decode_key`，手工 XOR 前 128 KiB：产出的 mp4 能播（`tools/m0-download-decrypt.mjs`，h264 1080x2344 + aac 7.48s，`ffmpeg -f null -` 零报错）
- [x] 把结论写回 `docs/project-background.md` 的「待确认的前提」，并更新本计划

**退出标准 —— 已达成**（2026-09-11）：手工串起来下载并解密出了一个可播放的视频文件。注入路线在 Linux 上成立，前提是给 webview 加上那个 spki switch。

### M1 — CLI 端到端

- [x] `wx-dlh proxy` —— 启动 MITM 代理，注入脚本，接收回传的 `decode_key` + URL（M0 就能用，M1 加了 `on_event` 钩子，`capture` 挂在上面）
- [x] `wx-dlh download <url> --key <decode_key>` —— 直连下载（不走代理）+ XOR 解密前 128 KiB，支持断点续传。另加 `--from-capture` / `--list`，免得手抄 600 字符的 URL；`--no-decrypt` 只取密文，配合 `wx-dlh decrypt` 排查
- [x] `wx-dlh decrypt <file> --key <decode_key>` —— 对已下载的密文文件单独解密，`--in-place` 只改头部，585 MB 的文件也不用复制一遍
- [x] `wx-dlh capture` —— 一条命令完成「注入 → 抓 key → 下载 → 解密」，抓够 `--count` 个就自己把代理停掉
- [x] keystream 来源二选一 —— **选了移植进 Python**，理由见下面「keystream 的取舍」
- [x] pytest 覆盖：JSAPI 响应解析（按 Linux 实测形态：8 条 media、带引号的 key）、keystream 与 `decrypt.js` 的 5 个黄金样本逐字节一致、XOR 边界（< / = / > 128 KiB 各配 5 种分块）、续传偏移、`decode_key` 全程字符串不丢精度

**退出标准 —— 已达成**（2026-09-11）：`wx-dlh capture` 一条命令从播放到可播放文件，`make lint test` 通过（241 项）。

#### keystream 的取舍：移植，不桥接

`decrypt.js` 里那 5.2 MB 的 wasm 就是**标准 ISAAC64**：把 `decode_key` 放进结果数组第 0 槽，`randinit` 走带 seed 的那条路径，取值顺序是 `rand()` 的顺序（结果数组从末尾往前），每个 64 位字按**大端**写出，直到 131072 字节。`wx_dlh/keystream.py` 是这个算法的 Python 实现（约 10 ms 一条 keystream），和 `decrypt.js` 的输出逐字节一致。

选它而不是起 Node 子进程，是因为：桥接要求装 Node、还要把不在本仓库里的 5.2 MB 产物找出来指给它，而移植版把整个运行时依赖降到零。风险（移植错了而不自知）由 `evals/README.md` 的黄金样本表挡着：表由 `decrypt.js` 生成，`tests/test_keystream.py` 每次跑都比对，`tools/keystream-golden.mjs` 可以重新生成这张表并顺手校验移植版。`tools/m0-download-decrypt.mjs` 留着，作为一条完全不经过 Python 的独立验证路径。

#### 下载用标准库，不引 httpx / aria2c

`urllib.request` 够用：流式读、`Range` 续传、自定义 UA 都有，而 `wx-dlh` 的运行时依赖因此保持为零（`mitmproxy` 只在 `proxy` / `capture` 用得上，仍是 optional）。真需要并行分片再说。落地细节：写 `.part`，写完才改名；`.part` 里存的是**已解密**的字节，所以续传时 `HeadDecryptor` 从该偏移接着套 keystream —— 这条是最容易写错的地方，`tests/test_downloader.py` 用 5 个偏移守着。

### M2 — 轻量 web server + 面板

- [x] 本地 HTTP server：捕获列表、代理状态、一键下载 —— `wx-dlh serve`（`wx_dlh/server.py`，标准库 `http.server`，运行时依赖仍然是零）
- [x] `web/` 前端页面（保持朴素，够用即可）—— `web/dashboard.html` + `web/dashboard.js`，原生 DOM，不引框架、不引 CDN、不铺构建工具
- [x] 与 CLI 复用同一套 `wx-dlh` 代码，不做第二份实现 —— 找记录用 `pipeline`，下载解密用 `downloader`，文件命名用同一个 `suggest_filename`，所以面板和 CLI 看到的是同一批文件

**退出标准 —— 已达成**（2026-09-11）：浏览器里能看到捕获列表并触发下载，实测通过。同日 `make lint test` 通过（282 项，原 241 项）。

#### 面板的三条实现约定

- **下载请求只能指名一条捕获记录，不能给 URL**。页面回传的是它刚看到的那条记录的裸 `url`，不在抓取记录里的一概拒绝。反过来做（接受客户端给的 URL）等于让浏览器里的任意页面驱使这个进程去抓任意地址、并把结果写进下载目录。
- **`decode_key` 出去是 JSON 字符串，而且不再回来**。它是 64 位整数，页面那侧 `JSON.parse` 碰上裸数字会静默丢精度 —— 这正是注入脚本在入口处用正则抠原文的原因（见 `wx_dlh.capture`）。面板只把它当文本显示，不做任何换算。`tests/test_server.py` 直接比对响应原文里那对引号。
- **一次只下一个**，理由和 `pipeline` 一样：两个 585 MB 并行下载对谁都没好处。

面板不负责起代理 —— 它只探一下代理端口通不通，然后把该跑的命令显示出来。两件事两个进程，互不依赖：`wx-dlh proxy` 在另一个终端里跑，面板每次请求都重读 `captures/`，所以不用告诉它代理什么时候抓到了东西。

### M3 — 视频后处理

- [x] 关键帧截屏（ffmpeg）—— `wx-dlh shots`，场景检测挑帧，太平的片子退回均匀取
- [x] 文字提取 —— **选了 OCR 这条路**（`wx-dlh extract`），理由见下面「文字提取选 OCR」
- [x] 内容总结 —— `wx-dlh summarize`，prompt 在 `wx_dlh/summarize.py` 的 `SYSTEM_PROMPT`，固定样本与评判标准在 `evals/summarize/`
- [x] `wx-dlh process <视频>` 一条命令走完三步，产物落在 `processed/<文件名>/`
- [x] pytest 覆盖：ffprobe 解析、场景输出解析与挑帧策略、OCR 行清洗与跨帧去重、请求体形状与每条错误路径、三步的复用与续跑（都用假 runner，CI 不需要 ffmpeg 或 tesseract）

**退出标准 —— 部分达成**（2026-09-11）：对 M0 那条 7.48 秒的样本，`shots` 出 5 张截屏 + `shots.json`，OCR 读回可读的中文（21 行，跨帧重复已合并），`summarize --dry-run` 打出完整请求体。**真实模型调用没跑过** —— 本机没有 `ANTHROPIC_API_KEY` 也没有 `ANTHROPIC_AUTH_TOKEN`，调用路径只被假 transport 和 `--dry-run` 覆盖。拿到凭据后按 `evals/summarize/README.md` 跑一遍，把结论填进那张表，这条才算真收口。

#### 文字提取选 OCR，不是字幕、也不是语音转写

三条路都能出文字，先选 OCR 是因为**这批内容的文字本来就在画面上**：

- **字幕流不存在。** 视频号下载下来的 mp4 没有字幕轨（实测样本 `ffprobe` 只有 h264 + aac），字幕是烧进画面的。真遇到带字幕轨的，`wx-dlh process` 会把它指出来 —— 那时候直接抽流比 OCR 准得多，不该让 OCR 去猜。
- **语音转写答非所问。** M0 的样本是一张文字卡片：内容全在画面上，没有旁白。转写这种片子只能得到背景音乐。
- **OCR 顺手。** 截屏这一步已经产出图了，OCR 只是每张多调一次 `tesseract` —— 不下模型、不加 Python 依赖、不要 GPU。

语音转写是记在册上的下一条路（讲解类、访谈类片子该走它），`text.json` 里的 `source` 字段就是为混合管线留的：哪几行是 OCR 读的、哪几行是转写来的，读的人分得清。

跨帧去重那条实现细节值得记一笔：同一张卡片会被连续几帧读到，按相似度合并；但**两行数字不同就不算重复**，哪怕其余部分一模一样。「填充第 0 行」和「填充第 1 行」相似度 0.917，按纯相似度会被删掉一条 —— 而 prompt 要求原样保留金额与版本号。留一条近似重复还能人工挑掉，删掉的数字找不回来。

#### 总结用标准库发请求，不引 anthropic SDK

SDK 是第一选择，但它**装不进同一个 venv**：`anthropic>=1` 要 httpx2 → httpcore2 → h11>=0.16，而 `mitmproxy` 把 h11 钉在更低的版本，`uv` 直接解不出来。真要了它，就会变成「能跑 `wx-dlh summarize` 的环境跑不了 `wx-dlh capture`」—— 一个工具裂成两个环境。退回 `anthropic<1` 能共存，但那是上一个大版本，`effort` 那套接口对不上。

所以请求用标准库 `urllib` 发（约三十行：拼 header、POST、读 JSON），和「keystream 移植不桥接」「下载用 urllib 不引 httpx」同一个取舍——**运行时依赖保持为零**。mitmproxy 哪天放开 h11 的钉子，这三十行换成 SDK 即可，理由记在 `wx_dlh/summarize.py` 的模块注释里。

`pyproject.toml` 里因此**没有** `summarize` extra，那里留了一条注释说明原因，免得下一个人以为是漏了。

## 技术选型（M0 后定稿）

| 环节 | 候选 | 取舍要点 |
|------|------|----------|
| MITM 代理 | **已定：`mitmproxy`**（optional 依赖 `.[proxy]`） | 能改写响应体、自带 CA 管理与 flow 存档；`wx_dlh.proxy` 只是一个 addon，mitmproxy 延迟导入，测试与 CI 不依赖它 |
| CA 信任 | 系统 CA 目录 / NSS / 客户端自带校验 | 本机 WeChat 是 Flatpak：宿主机 `update-ca-trust` 它看不到（runtime 自带证书包），webview 是 Chromium 内核、用 NSS 库。`scripts/trust-ca.sh` 三个位置都覆盖，哪个生效 M0 验 |
| keystream | **已定：移植 ISAAC64 到 Python**（`wx_dlh/keystream.py`） | 与 `decrypt.js` 输出逐字节一致，Node 与 5.2 MB wasm 都不再需要；黄金样本表守着移植的正确性 |
| 下载 | **已定：标准库 `urllib.request`** | 流式 + `Range` 续传够用，运行时依赖保持为零；`.part` 里是已解密字节，续传按偏移接 keystream |
| web server | **已定：标准库 `http.server`** | 面板很薄，不值得为它引入大框架；`ThreadingHTTPServer` 够用，运行时依赖因此保持为零 |
| 截屏/提取 | **已定：`ffmpeg` 场景检测 + `tesseract` OCR** | 两个都是外部命令，不进 Python 依赖；OCR 只是给已有的截屏多调一次，不下模型、不要 GPU。语音转写留作下一条路 |
| 总结 | **已定：标准库 `urllib` 直发 Messages API** | anthropic SDK 与 mitmproxy 的 h11 版本冲突，装了就会把 capture 和 summarize 拆成两个环境；prompt 跟着包走，样本在 `evals/summarize/` |

## 边界

见 `AGENTS.md` 的 Boundaries：仅限本人客户端已可播放内容的个人使用；不做付费/受限内容的 DRM 破解、不做他人账号批量抓取、不再分发抓取到的媒体。

## 已定下来的实现细节

这几条是写代理时定的，和参考实现有意不同，理由记在这里免得以后当成 bug 改回去：

- **注入脚本走页面同源**：`<script src="/__wx-dlh/inject.js">`，代理拦下 `/__wx-dlh/*` 自己回，不转发上游。参考实现从 `res.wx.qq.com` 取脚本、往哨兵域名 `aaaa.com` 回传，是为了绕页面 CSP；同源既满足 `script-src 'self'`，回传也不触发 CORS 预检。注入时同时删掉响应的 CSP 头作为双保险。
- **`decode_key` 用正则从响应原文里抠**：`/"decode_key"\s*:\s*("?)(\d{1,20})\1/`。只要经过 `JSON.parse`，裸数字形式的 key 就已经是 float 了（`13835058055282163712` → `13835058055282164000`），而且不报错。引号有没有一并记成 `decode_key_json_type`，正好是 M0 要回答的问题之一。`tools/check-inject.mjs` 专门守着这条。
- **默认不 MITM `(long|short|minorshort|dns).(wechat.com|weixin.qq.com)`**：那是 mmtls，不是 TLS，让它原样穿过，别把登录搞坏（`--ignore-host` 可改）。本机客户端用的是 `*.wechat.com` 那半边 —— 第一轮只挡了 `*.weixin.qq.com`，结果 276 个 mmtls 请求被白拦，视频也没播出来。
- **给 webview 加 Chromium switch 靠 LD_PRELOAD**：`scripts/appex-flags-preload.c` 钩 `execv`/`execve`/`execvp`/`posix_spawn`，只对程序名匹配 `WX_DLH_APPEX_TARGET`（默认 `WeChatAppEx`）的 exec 追加 `WX_DLH_APPEX_FLAGS` 里的参数，已在 argv 里的 switch 不重复追加（Chromium 会 re-exec 自己拉子进程）。.so 放在 `~/.var/app/<app-id>/.wx-dlh/` 并 `--persist=.wx-dlh`，沙箱里才看得见。
- **spike 期间日志开得很大**：每条 flow 的摘要、注入前的原始页面、以及注入脚本看到的**每一次** `invoke`（只记 api 名、`err_msg` 和顶层 key 名，不记值）。Linux 上那个 `err_msg` 字符串就是这么找出来的。

这几条是 M1 写下载与 capture 时定的：

- **`.part` 里存的是已解密的字节**，不是密文。所以续传时 `HeadDecryptor` 要用文件里已有的长度作为 keystream 偏移；写成从 0 开始会得到一个大小正确、播起来是雪花的文件，而且不报错。
- **`media[]` 的选择权交给操作者**：一次 capture 带整屏 feed（实测 8 条），只有人知道刚才播的是哪一个。默认跟参考实现一样取 `media[0]`，`--index` / `--all` 覆盖其余情况。
- **去重按裸 `url`，不按 `download_url`**：`url_token` 每次 capture 都换一份，拿全地址去重等于不去重，同一个视频会随着滚动被反复下载。文件名里的摘要同理，用裸 `url` 算。
- **下载不经过代理**，这条从参考实现继承：代理的职责是把 `decode_key` 弄出来，不该再驮着几百 MB 的视频。UA 用桌面版 Chrome 那串，和参考实现一致。
- **`wx-dlh capture` 抓够就自己停**：`proxy.run()` 多了一个 `on_ready`，把一个线程安全的 stop 交给调用方；下载在单独的工作线程上跑，抓够 `--count` 个就从那里把代理关掉。

## 跟踪

- 讨论：https://github.com/amyteq/at_wxclip/discussions/2
- M0（已完成）：https://github.com/amyteq/at_wxclip/issues/4 —— Linux 侧的结论、踩过的坑和跨平台问答都在那条 issue 里
- M1（已完成）：https://github.com/amyteq/at_wxclip/issues/8
- 跨平台 adapter：https://github.com/amyteq/at_wxclip/issues/11 —— 从 M1 拆出来。没有 Win/Mac 机器实测，照抄参考实现只会留下一段没人跑过的代码；M0 在 Linux 上的教训就是「装了 CA、命令全成功、客户端照样不认」，没实测的 adapter 只会给下一个人一个假的起点
- M2（已完成）：https://github.com/amyteq/at_wxclip/issues/9
- M3（代码完成，真实模型调用待验）：https://github.com/amyteq/at_wxclip/issues/10
- PR：骨架 https://github.com/amyteq/at_wxclip/pull/3 、M0 https://github.com/amyteq/at_wxclip/pull/7

里程碑勾选在各自的 issue 里维护。
