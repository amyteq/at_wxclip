# at_wxclip

把 Linux 版 WeChat 里能播放的视频号视频抓下来、解密成可播放文件，再做截屏、文字提取和总结。一个 headless 命令行工具 + 一个轻量 web server 作为 GUI。

命令行工具与 Python 包叫 **wx-dlh**（原名 wxclip，[#11](https://github.com/amyteq/at_wxclip/issues/11) 里改的名），仓库名 `at_wxclip` 不变。从旧名升级见下面「从 wxclip 升级」。

## 工作原理

机制以参考实现 [`WeChatVideoDownloader`](https://github.com/harrysun2006/WeChatVideoDownloader) 的代码为准（已逐行核对，见 `docs/project-background.md`）：

```text
① WeChat 打开视频号页面
        │
        ▼
② wx-dlh MITM 代理（必须安装并信任本地 CA，否则看不到 HTTPS 内容）
        ├── 改写 channels.weixin.qq.com 的 HTML 响应，在 </body> 前注入一个 <script>
        └── 脚本走页面同源 /__wx-dlh/inject.js，由代理自己应答
            （参考实现是顶替 res.wx.qq.com 上某个脚本的响应体）
        │
        ▼
③ 注入脚本 hook window.WeixinJSBridge.invoke，从 JSAPI 响应里取出每条
   media[] 的 url + url_token、decode_key、file_size、描述、作者
   （实测一次 capture 带 8 条，不是一条；参考实现只取 media[0]）
        │
        ▼
④ 回传给 wx-dlh：POST 同源的 /__wx-dlh/capture，代理拦下，不转发上游
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

1. **decoder 不是「解析真实下载地址」，而是解密已下载文件的头部。** 下载地址本身不需要解析，它就是 JSAPI 响应里的 `url + url_token`；`decrypt.js` 只负责用 `decode_key` 生成 keystream。项目介绍讨论里「再经过 decoder 来解析出真实的下载地址」这句话与参考实现的代码不一致，以代码为准。
2. **只被动抓 URL 是不够的。** `decode_key` 不出现在任何网络请求里，只出现在 `WeixinJSBridge` 的 JSAPI 响应中，所以参考实现要注入脚本去 hook 它。拿到 URL 却没有 `decode_key`，下载到的文件前 128 KiB 是密文，播不了。
3. **`decode_key` 全程当字符串传。** 它是 64 位整数，wasm 侧签名是 `std::string`；一旦被当成 JS number 解析就会丢精度，keystream 完全不同而且不会报错。实测 `13835058055282163712` 与其 number 化后的 `13835058055282164000` 生成的 keystream 毫无关系。

## 技术栈

- **Python**：MITM 代理（注入 + 抓取 `decode_key`）、keystream、直连下载、XOR 解密、以及提供管理与下载面板的 web server。运行时**只有一个第三方依赖** `mitmproxy`（代理与 CA），其余都是标准库：下载走 `urllib`，面板走 `http.server`
- **JavaScript**：注入到视频号页面里的 hook 脚本 `web/inject.js`
- **假设 WeChat 运行在同一台 Linux 机器上** —— 注意参考实现只支持 Win/Mac（代理设置走 Windows 注册表或 macOS `networksetup`，没有 Linux 分支），Linux 侧是自己打通的，见 `docs/project-background.md`

**keystream 不再需要 Node。** 参考实现把它交给 `decrypt.js`（5.2 MB 的 Emscripten 产物，wasm 以 base64 内嵌，导出 `getDecryptionArray(seedString)`）；M1 把它认出来是标准 ISAAC64，用 `decode_key` 作种子填进结果数组第 0 槽，每个 64 位字按**大端**吐出 131072 字节。`wx_dlh/keystream.py` 就是这个算法的 Python 实现，和 `decrypt.js` 的输出**逐字节一致**——`evals/README.md` 的黄金样本表由 `decrypt.js` 生成，`tests/test_keystream.py` 每次都比对，`tools/keystream-golden.mjs` 可以重新生成这张表并顺手校验移植版。

## 快速开始

### Python 环境

需要 Python 3.11+。推荐用 [uv](https://docs.astral.sh/uv/) 或 [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html) 管理，它们怎么安装以各自的官方文档为准，这里不重复。

- **uv**：`make setup` 会直接用它建 `.venv`（没有 uv 时回落到 `python3 -m venv` + pip）。
- **micromamba**：自己建一个 Python ≥ 3.11 的环境并激活，在仓库根目录 `pip install -e '.[dev]'`；之后跑 `make lint test PY=python`（Makefile 默认找 `.venv/bin/python`）。

### 只想用，不改代码

```bash
pip install wx-dlh                                             # 发布到 PyPI 之后
pip install "wx-dlh @ git+https://github.com/amyteq/at_wxclip"  # 现在（仓库是私有的，要有访问权限）
```

一条命令就装齐，`capture` / `proxy` 要用的 mitmproxy 是默认依赖。想装成独立的命令行工具就用 `pipx install wx-dlh` 或 `uv tool install wx-dlh`。信任 CA 也在这条命令里（`wx-dlh ca --install`，见下）；`scripts/` 下的辅助脚本不在包里，用 pip 装的人只有 Linux 上「带代理启动 WeChat」那一套（`run-wechat-proxied.sh` 和它编译的 preload shim）要从仓库取。发布流程见 `docs/pypi-publishing.md`。

### 开发

```bash
make setup          # 创建 .venv 并安装依赖（uv，回落到 pip；含代理用的 mitmproxy）
make lint test      # 检查 + 测试
python -m wx_dlh --help
```

准备工作两步，细节和判定方法见 `docs/M0-runbook.md`：

```bash
.venv/bin/python -m wx_dlh ca             # 生成本地 CA，打印指纹，列出这台机器有哪些信任库
.venv/bin/python -m wx_dlh ca --install   # 装进探测到的信任库（本机 WeChat 是 Flatpak → 沙箱内 NSS）
```

`wx-dlh ca --install` 按操作系统挑信任库，没有 CA 时先生成一张；装完都会去真库里回查一次（Linux 上「命令全成功、客户端照样不认」的教训）。`--list` 查看、`--remove` 撤销，两者都按指纹认证书：同名的旧 CA 会被说成「another CA」，不会冒充当前这张，`--remove` 也只删指纹对得上的那一张——同名（或同文件名）但属于别的 CA 的那张只报告、不删，读不出来的也不删，并且退出码非 0。**库本身查不动**（NSS 库打不开、钥匙串打不开、certutil 查不了 Root 库）同样算失败：不会说成「没装」「没什么可删的」，退出码非 0 —— 查不动的库里那张 CA 可能还在。`--cert` 给的文件会先交给 openssl 读一遍，而且要求 openssl 重新编码出来的字节跟文件里的一模一样，才会进信任库：带私钥的、PEM 壳里装着别的东西的、一个文件里塞了好几张的、证书后面还跟着别的字节的、以及不是 CA 证书的（信任库装的是信任锚），都在碰信任库之前就拒掉；长度、标签写法不合 DER 的也拒——openssl 会把它们悄悄改写之后再算指纹，这边按文件原样算，两个指纹对不上，装进去也就再也查不到、删不掉。要修就按提示跑 `openssl x509 -in FILE -out FILE` 重写一遍。给了 `--flatpak` / `--user` / `--system` 之类的参数就**只**装那几个，不是在默认之上再加一个，要全装用 `--all`。

**别用 `sudo` 跑它**：它只在 `--system` 时自己要 sudo，整个命令跑在 root 下会把 root 的家目录和钥匙串卷进来，所以会直接拒绝（WSL 也一样——它装的是 Windows 宿主机的证书库，但自己是 POSIX，sudo 还是 sudo）。`--cert` 只收**纯证书**的 `mitmproxy-ca-cert.pem`：带私钥的 `mitmproxy-ca.pem` 会被当场拒掉，不然 `--system` 会把 CA 私钥按 0644 装进系统信任锚目录。

`scripts/trust-ca.sh` 是同一套逻辑的 shell 版，参数完全一样（`scripts/trust-ca.sh --install --system` ≡ `wx-dlh ca --install --system`），留给本机没装 wx-dlh 的情况，比如 wx-dlh 跑在 Docker 里、宿主机只需要信任 CA。**它要有 openssl**：证书先用 openssl 解析过才会进信任库，查库、删库也都按它算出来的指纹；没有 openssl 就直接报错退出，不会把一个没读过的文件装进信任库。`wx-dlh ca` 不需要 openssl。

| 系统 | 默认（不要 sudo） | `--system` | 状态 |
|------|------------------|-----------|------|
| Linux | Flatpak 沙箱 NSS + `~/.pki/nssdb` | 发行版的信任锚目录：Arch、Fedora/RHEL（`update-ca-trust`）、Debian/Ubuntu、openSUSE（`update-ca-certificates`） | 已实测（M0） |
| macOS | 登录钥匙串，仅当前用户信任（会弹密码框） | 系统钥匙串，admin 域信任（sudo；参考实现用的就是它） | **未在真实 WeChat 上验证**（#11） |
| Windows | `CurrentUser\Root`（certutil，会弹确认框） | `LocalMachine\Root`（要「以管理员身份」开的 shell） | **未验证**（#11） |

Windows 上用 Windows 自己的 Python 跑就行，不需要 Git Bash（shell 版才要 Git Bash / MSYS2 / Cygwin）；**在 WSL 里跑也是装进 Windows 宿主的证书库**，因为 WeChat 跑在宿主上，WSL 发行版自己的信任库它根本不看（`--os linux` 可以改回去）。查找证书按指纹，不按名字（所有 mitmproxy CA 都叫 `mitmproxy`）；指纹由 Python 自己算，不需要 openssl（shell 版要）。

然后一条命令从播放到可播放文件（默认抓到第一个就停，文件落在 `downloads/`）：

```bash
.venv/bin/python -m wx_dlh capture           # 起代理 → 注入 → 抓 key → 下载 → 解密
.venv/bin/python -m wx_dlh capture --count 3 --max-size 200m --wait 120
```

拆开来用也行：

```bash
wx-dlh proxy                                 # 只抓，不下载；事件写进 captures/
wx-dlh download --list                       # 看最近一次抓到了哪些 media
wx-dlh download --from-capture --index 2     # 按序号直连下载（不走代理）+ 解密
wx-dlh download <url> --key <decode_key>     # 手工给地址和 key
wx-dlh decrypt <file> --key <decode_key>     # 只对已下载的密文重跑解密
```

下载是流式的、带进度、可断点续传：中断会留下 `.part`，再跑一次接着下（`.part` 里存的已经是解密后的字节，续传时 keystream 会从该偏移接上）。`file_size` 实测跨度 3.8 MB – 585 MB，所以任何时候都不会把整个文件读进内存。

### 面板（浏览器里挑）

一次 capture 会带回整屏 feed（实测 8 条），命令行里只能靠 `--index` 猜编号；面板就是为这件事来的 —— 一眼看完再点哪条：

```bash
.venv/bin/python -m wx_dlh serve            # 默认 http://127.0.0.1:8080
.venv/bin/python -m wx_dlh serve --port 9000 --dir ./downloads
```

它和 `wx-dlh proxy` 是两个进程：面板只探一下代理端口通不通，并把该跑的命令显示出来。典型用法是两个终端，一个跑 `wx-dlh proxy`，一个跑 `wx-dlh serve`，然后在 WeChat 里播视频、在浏览器里点下载。面板每次请求都重读 `captures/`，所以不用重启它。

面板上有：代理状态、捕获列表（说明、大小、`decode_key`、是否已下载）、下载进度、以及下载目录里的文件（可以直接播或另存为；`/files/` 支持 Range，所以拖进度条也没问题）。

**面板不接受 URL**，只接受它刚展示过的那条记录的裸 `url`，不在抓取记录里的一概拒绝 —— 否则浏览器里的任意页面都能驱使这个进程去抓任意地址并写进下载目录。同理，`decode_key` 出去是 JSON 字符串，页面只把它当文本显示：裸数字一经 `JSON.parse` 就会静默丢精度。

下载用的仍然是 `wx-dlh download` 那套代码、同一套文件命名，所以面板和 CLI 看到的是同一批文件：已经下过的那条会标上「已下载」、按钮变成「重新下载」—— 明确点它才会再抓一遍。文件是在上一次轮询之后才出现的话（比如 CLI 那边刚下完），服务端会直接回「已存在」，不会重抓 585 MB。

代理运行时的产物都在 `captures/`（git 忽略）：`traffic-*.jsonl` 每条 flow 的摘要、`captures-*.jsonl` 注入脚本回传的事件（含 `decode_key`）、`pages/*.html` 注入前的原始页面、`flows-*.mitm` 完整 flow 存档（`mitmproxy -r` 回看）。

同源的 `/__wx-dlh/*` 比参考实现的 `res.wx.qq.com` + 哨兵域名 `aaaa.com` 少一层 CSP 与跨域风险，代价是页面必须是 HTML 响应。M0 已经确认 Linux 客户端走的就是 `channels.weixin.qq.com` 的 H5 页面，注入路线成立。

### 用 Docker 跑

Python 环境、mitmproxy、代理、面板和 `capture` 也可以都放进容器（`python:3.12.12-slim-trixie`）：

```bash
docker compose up -d --build        # 代理 127.0.0.1:8899，面板 http://127.0.0.1:8080
```

**省掉的是宿主机上的 Python 环境，省不掉 CA 信任和 WeChat 的代理设置**：这两件事发生在宿主机的 WeChat 客户端上，容器碰不到。CA 默认放在宿主机的 `~/.config/wx-dlh/ca`，和原生安装同一个目录，所以 `scripts/trust-ca.sh`、`run-wechat-proxied.sh` 不用改参数，信任过的 CA 也接着有效。信任这一步在宿主机上做，用的是宿主机的 openssl（`trust-ca.sh` 没有它就报错退出）；宿主机本来就装了 wx-dlh 的话，`wx-dlh ca --install` 是同一件事，不需要 openssl。细节、各系统的步骤和验证范围见 `docs/docker.md`。

### 从 wxclip 升级

- 重新 `make setup`，命令变成 `wx-dlh`（或 `python -m wx_dlh`）。
- CA 不用重新生成、也不用重新信任：第一次跑 `wx-dlh ca` / `proxy` / `capture` 时，`~/.config/wxclip/ca` 会被原样搬到 `~/.config/wx-dlh/ca`（同一对文件、同一把私钥），终端里会提示一行；旧路径留一个指向新目录的软链接，切回旧版本的 checkout 也还是同一张 CA。
- 环境变量前缀从 `WXCLIP_` 改成 `WX_DLH_`，`.env` 里的要跟着改（模板见 `.env.example`）；旧名字不再读取。
- Linux webview 的 shim：已有的 flatpak override 不受影响、照常工作；想换成新名字（`~/.var/app/<app-id>/.wx-dlh/`、`WX_DLH_APPEX_FLAGS`）就重跑一次 `scripts/run-wechat-proxied.sh --persist`。

## 视频后处理：截屏 / 文字 / 总结

拿到可播放文件之后，一条命令走完三步，产物落在 `processed/<文件名>/`（不进版本库）：

```bash
wx-dlh process downloads/xxx.mp4              # 截屏 → OCR → 总结
wx-dlh process downloads/xxx.mp4 --no-summary # 只到文字，不调模型、不需要凭据
```

```text
processed/xxx/
├── shots.json              # 选了哪几帧、为什么选、当时的场景分
├── shot-001-t0.00s.jpg …   # 关键帧截屏
├── text.txt                # 去重后的文字，喂给模型的就是它
├── text.json               # 每张截屏各读出什么，原始输出也留着
└── summary.md              # 一段总结（含模型、档位、token 数）
```

三步也能单独跑，产物互相复用 —— 改 prompt 重跑一次总结，不会重新截屏、重新 OCR：

```bash
wx-dlh shots <视频>                    # 只截屏
wx-dlh extract --check                 # 先看 OCR 能不能跑（缺哪个语言包）
wx-dlh extract <视频|目录>             # 只提文字
wx-dlh summarize <视频|目录>           # 只总结
wx-dlh summarize <目录> --dry-run      # 把请求体打出来看，不发请求
```

**截屏**用 ffmpeg 的场景检测（`select=gt(scene,0.2)`）挑关键帧，检测出的变化太少就退回按时长均匀取，所以再平的片子也有截屏。检测只解一遍码，取帧靠 `-ss` 关键帧跳转。

**文字提取先走 OCR 这条路**：视频号下载下来没有字幕流（字幕是烧进画面的），而实测样本本身就是一张文字卡片 —— 内容全在画面上，没有旁白。截屏已经有了，OCR 只是每张多调一次 `tesseract`，不下模型、不加 Python 依赖。同一张卡片跨帧重复的行会按相似度合并，但**数字不同就不算重复**（宁可留一条近似重复，也不能把金额、版本号删掉）。语音转写是记在册上的下一条路，`text.json` 里的 `source` 字段就是留给它区分的。

```bash
# 缺中文语言包时 extract --check 会把两种装法都告诉你
sudo pacman -S tesseract-data-chi_sim tesseract-data-eng     # Arch
wx-dlh extract <目录> --tessdata-dir ~/.local/share/tessdata # 不用 root 的那条路
```

**总结**把文字和几张截屏（跨全片均匀取，不是取前几张）一起发给 Messages API。截屏在这里不是装饰：OCR 把「折叠屏」读成「折玛屏」「折私屏」，模型对着图才知道该改成什么。prompt 在 `wx_dlh/summarize.py` 的 `SYSTEM_PROMPT`，固定样本和评判标准在 `evals/summarize/`。凭据从环境变量或 `.env` 读 `ANTHROPIC_API_KEY`（或 `ANTHROPIC_AUTH_TOKEN`）。

这一步**没有引入 Python 依赖**：anthropic SDK 装不进同一个 venv（它要 httpx2 → httpcore2 → h11>=0.16，mitmproxy 把 h11 钉在更低的版本），真装了就会变成「能总结的环境跑不了 capture」。所以请求用标准库 `urllib` 发，理由写在 `wx_dlh/summarize.py` 的模块注释里。

## 目录

| 路径       | 说明                                                    |
|------------|---------------------------------------------------------|
| `wx_dlh/`  | Python 包：proxy / capture / keystream / decrypt / downloader / pipeline / server、CA 与信任库 ca / trust、后处理 media / frames / extract / summarize / postprocess |
| `web/`     | 注入脚本与前端页面                                       |
| `docs/`    | 背景、开发计划、规格说明                                 |
| `tests/`   | pytest 测试                                              |
| `scripts/` | 给人手动执行的入口脚本                                   |
| `tools/`   | 辅助小工具，不属于交付包                                 |
| `evals/`   | 解码/提取/总结质量的固定样本与校验                        |
| `.claude/skills/` | 本仓库自带的 Claude Code skill（`at-review-pr` / `at-refine-issue`），见下一节 |

详见 `docs/directory-guide.md`；智能体协作约定见 `AGENTS.md`。

## Claude Code 项目 skill

仓库自带两个开发流程 skill，正文在 `.claude/skills/<name>/SKILL.md`，`.agents/skills/` 下是指向它们的仓库内相对软链接。两个 skill 自身都不往仓库里加运行时代码。写入范围不一样，别弄混：`at-refine-issue` 只读写 issue 正文、评论与 label，永远不碰代码；`at-review-pr` 除了发布 review 与处置报告，还会在已批准的范围内启动一个 writer 会话去改**被 review 的那条 PR** —— 该 PR 涉及的源码、测试、文档都可能被改动，并在已授权时提交、推送到该 PR 的 head 分支。

| Skill | 调用 | 做什么 |
|-------|------|--------|
| `at-review-pr` | `/at-review-pr [pr=<n>] [rounds] [reviewer] [writer]` | 用互相独立的 Codex / Claude Code 会话跑有界的「review → 修/申辩/推迟 → 再 review」循环，每条发现有稳定 ID 与状态机，批准前必须对最终 head 跑一次完整 review |
| `at-refine-issue` | `/at-refine-issue <issue-url\|number> [说明]` | 把一条已有 issue 改写成工单：编号稳定的验收标准（`AC1`…`ACn`）、一条自包含的任务 comment、以及可复制的启动 prompt |

参数默认值：`at-review-pr` 的 `rounds` 默认 `10`、`reviewer` 默认 `codex`、`writer` 默认 `claude`；`pr=<n>` 省略时按「显式上下文 → 当前分支唯一的 open PR」解析，解析不出来就问，不猜。`at-refine-issue` 的第一个参数是 issue URL 或编号，正常情况下必须给；只有当会话里已经明确、无歧义地选定了唯一一条 issue 时才允许省略，两条候选或「我们刚才说的那条」一律要问、不猜。后面跟可选的细化说明。

两个 skill 都不会自己派工：`at-refine-issue` 产出的工单在人发出启动 prompt 之前是死的，`at-review-pr` 也不会合并 PR。

**Codex 侧要显式指路。** Codex CLI 会向上查找 `.agents/skills`，但不会像 Claude Code 那样把 skill 当斜杠命令自动挂上来，所以在 Codex 里用的时候，直接把文件指给它，例如 `按 .claude/skills/at-review-pr/SKILL.md 跑 PR #14 的 review 循环`。软链接只是让 `.agents/skills/at-review-pr/SKILL.md` 这条路径也能读到同一份正文。

## 参考

- 参考实现：https://github.com/harrysun2006/WeChatVideoDownloader（上游 lecepin/WeChatVideoDownloader，v2.x 起支持加密视频）
- keystream 模块（本项目已移植进 Python，这里留作黄金样本的来源）：[`electron/decrypt.js`](https://github.com/harrysun2006/WeChatVideoDownloader/blob/master/electron/decrypt.js)
- 项目介绍讨论：https://github.com/amyteq/at_wxclip/discussions/2
- 开发计划 issue：M0 https://github.com/amyteq/at_wxclip/issues/4 、M1 https://github.com/amyteq/at_wxclip/issues/8 、M3 https://github.com/amyteq/at_wxclip/issues/10

## 说明

仅用于下载本人 WeChat 客户端中已可正常播放的内容，供个人使用。不做付费/受限内容的 DRM 破解，不做他人账号的批量抓取，不再分发抓取到的媒体文件。

## 许可证

MIT，全文见仓库根目录的 `LICENSE`。
