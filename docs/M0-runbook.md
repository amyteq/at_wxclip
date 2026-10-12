# M0 操作步骤（Linux / Arch，WeChat 4.0.1.11）

M0 要回答两个问题：**Linux 版 WeChat 能不能被 MITM**，以及**视频号是不是还走那条可注入的 H5 路径**。代理、CA、注入脚本已经就绪（`wx-dlh proxy`），剩下的是需要真人操作的部分：让 WeChat 信任 CA、把代理设上、登录、播一个视频号视频。

## 结论：两个问题都是「能」（2026-09-11 15:01 实测通过）

端到端跑通了：注入落地 → `window.WeixinJSBridge` 可 hook → 拿到 `decode_key` → 直连下载 → XOR 前 128 KiB → `ffprobe` 认出的可播放 mp4（`ffmpeg -f null -` 零报错）。

**但有一个 Linux 独有的前提**：webview 的根证书编译在 `WeChatAppEx` 二进制里，**装 CA 对它永远无效**，必须给它加 `--ignore-certificate-errors-spki-list`（第 3.4 节）。装 CA 那一步（第 1 节）仍然要做，那是给**主进程**的。

下面的步骤和判定表按实际跑通的顺序保留，复现时照着走：1 → 2 → 3 → 3.4 → 4。

本机实测到的前提（`flatpak info com.tencent.WeChat`、`pgrep -af wechat`）：

- WeChat 是 **Flatpak** 包（`com.tencent.WeChat` 4.0.1.11，runtime `org.freedesktop.Platform 25.08`），沙箱权限只有 `xdg-download:ro`，**看不到宿主机的家目录**，HOME 实际是 `~/.var/app/com.tencent.WeChat/`。
- 视频号页面跑在 `RadiumWMPF/runtime/WeChatAppEx` 里 —— 进程参数里有 `network.mojom.NetworkService`、`gpu-process`、`--enable-features=...`，是 **Chromium 内核的 webview**。注入路线在 Linux 上有戏就是因为这个。
- Flatpak 沙箱里的 `/etc/pki` 来自 runtime 自带的证书包（146 张根证书，宿主机的 `/etc/ssl/certs` 在沙箱里根本不存在），**所以宿主机 `update-ca-trust` 装进去的 CA 它看不到**。**主进程**的信任库是沙箱内的 NSS：`~/.var/app/com.tencent.WeChat/.pki/nssdb`。
- **webview 是个例外，而且没得谈**：`WeChatAppEx` 是 Chromium 122，根证书列表编译在它自己的二进制里，做证书校验的那个进程从来不打开 NSS 数据库。装 CA 对它完全无效，只能给它加 `--ignore-certificate-errors-spki-list`（第 3.4 节）。详细证据在 `docs/project-background.md`。
- **光把文件放到那个路径没用。** Flatpak 的家目录只挂载 metadata 里 `persistent=` 列出的子目录（WeChat 是 `.xwechat` 和 `xwechat_files`），`.pki` 不在里面 —— 从沙箱里看 `$HOME/.pki/nssdb` 根本不存在。必须补一条：

  ```bash
  flatpak override --user com.tencent.WeChat --persist=.pki
  ```

  这一步现在由 `scripts/trust-ca.sh` 自动做掉，并且会真的进沙箱确认一次（`flatpak run --command=sh … -c '[ -f $HOME/.pki/nssdb/cert9.db ]'`）。这个坑很安静：证书装了、命令全成功、应用照样拒绝，表现就是 webview 弹 **「您的连接不是私密连接」**，代理日志里是 `tls alert certificate unknown`。

## 第一轮实测结果（2026-09-11 11:25–11:31 本机时间）

应用内代理设成 `127.0.0.1:8899` 后登录、打开一个视频号视频。**视频没播出来**，但这一轮拿到了 M0 最想要的那个答案：

- ✅ **CA 被接受了**。`wx.qlogo.cn`、`mmhead.c2c.wechat.com`、`dl.wechat.com`（3 MB 资源更新）、`inews.gtimg.com` 的 HTTPS 全部被成功 MITM 出明文，没有一次 TLS 失败。
- ✅ **H5 路线在 Linux 上是存在的**，而且 URL 和参考实现一致。webview 打开过 `https://channels.weixin.qq.com/web/pages/feed?oid=…&nid=…&context_id=…&entrance_id=1002&exportkey=…`，标题「视频号」，11:26:25 和 11:27:07 各一次，并在 11:27:07 落了 `channels.weixin.qq.com` 的 cookie。证据在 WeChat 自己的 Chromium profile 里：`~/.var/app/com.tencent.WeChat/.xwechat/radium/web/profiles/multitab_<hash>/{History,Cookies}`。
- ❌ **但这些请求一条都没走代理**。同一时刻代理日志里只有头像和 mmtls。`WeChatAppEx` 的 renderer 进程（11:25:54 / 11:26:22 / 11:27:04 起）既没有 `--proxy-server` 参数，也没有任何 proxy 环境变量 —— **应用内的代理设置只作用于主进程，webview 直连**。
- 主进程的流量长这样：`sgshort.wechat.com:80` 上 276 条 `POST /mmtls/<id>`（全 200），即 WeChat 自己的 mmtls 传输套在 HTTP 里。**注意 host 是 `*.wechat.com`，不是参考实现那套 `*.weixin.qq.com`** —— 代理的默认忽略名单已按此修正。

所以第二轮要做两件事：把 webview 也塞进代理（下面第 3 节），以及先确认视频播不出来到底是不是代理造成的（第 3.5 节）。

## 1. 准备 CA（已做，可重复执行）

```bash
.venv/bin/python -m wx_dlh ca          # 生成 CA 并打印证书路径、指纹、可用的信任位置
scripts/trust-ca.sh                    # 装进自动探测到的 NSS 库（Flatpak 沙箱优先）
scripts/trust-ca.sh --list             # 看装在哪了
scripts/trust-ca.sh --remove           # 撤销
```

CA 默认在 `~/.config/wx-dlh/ca/`（**不在仓库里**，因为带私钥）。`WX_DLH_CA_DIR` 可以改。装完要**重启 WeChat**，证书库是启动时读的。

如果之后发现 WeChat 仍然不认这张证书，按这个顺序试：

```bash
scripts/trust-ca.sh --system           # 宿主机系统信任库（sudo；Flatpak 看不到，但 /opt/wechat 那个包要）
# 让沙箱里的 OpenSSL 类调用也认（Chromium 的 NSS 不吃这招，但值得一并排除）
flatpak override --user com.tencent.WeChat \
  --filesystem="$HOME/.config/wx-dlh:ro" \
  --env=SSL_CERT_FILE="$HOME/.config/wx-dlh/ca/mitmproxy-ca-cert.pem"
flatpak override --user --reset com.tencent.WeChat   # 撤销
```

## 2. 启动代理

```bash
.venv/bin/python -m wx_dlh proxy                    # 默认 127.0.0.1:8899
.venv/bin/python -m wx_dlh proxy --host 0.0.0.0     # 只有 WeChat 不在本机时才需要
.venv/bin/python -m wx_dlh proxy --dry-run          # 只打印计划，不监听
```

代理会做三件事：

1. 把**每一条 flow** 摘要写进 `captures/traffic-*.jsonl`（方法、host、path、状态、content-type、是否注入），并把完整 flow 存成 `captures/flows-*.mitm`（用 `mitmproxy -r <file>` 回看）。这就是 M0 的证据。
2. 给 `channels.weixin.qq.com` 的 HTML 响应在 `</body>` 前插 `<script src="/__wx-dlh/inject.js">`，同时**删掉该响应的 CSP 头**；`/__wx-dlh/*` 的请求由代理自己回，不转发上游。脚本走**页面同源**，比参考实现的 `res.wx.qq.com` + `aaaa.com` 少一层 CSP 和跨域风险。原始页面另存为 `captures/pages/*.html`。
3. 收注入脚本回传的 `probe` / `hook` / `invoke` / `capture` 事件，写进 `captures/captures-*.jsonl`，`capture` 事件在终端直接打出 `decode_key` 和下载地址。

`captures/` 与 CA 都在 `.gitignore` 里 —— 里面是本人的会话内容，不要提交。

## 3. 让 WeChat（**包括 webview**）走代理

两半都要，缺一不可：

**主进程** —— 设置 → 通用设置 → 网络代理 → HTTP 代理，填 `127.0.0.1` / `8899`（不需要用户名密码）。Flatpak 默认 `shared=network`，沙箱里的 `127.0.0.1` 就是宿主机的回环地址；不生效再试 `192.168.18.88`（本机 wlo1）。

**webview** —— 应用内那个设置**管不到**它（第一轮实测，见上）。Chromium 在没有其他代理配置时会回落到 `http_proxy` / `https_proxy` / `all_proxy` 环境变量，而 Flatpak 应用会继承 `flatpak run` 给的环境变量，所以：

```bash
# 先把 WeChat 完全退出（托盘 → 退出），然后：
scripts/run-wechat-proxied.sh            # = flatpak run --env=http_proxy=... com.tencent.WeChat
scripts/run-wechat-proxied.sh --kill     # 或者让脚本替你 flatpak kill 再启
```

环境变量只对新的进程树生效，所以必须整个应用重启，不能只关窗口。想让它长期生效可以用 `flatpak override --user com.tencent.WeChat --env=http_proxy=http://127.0.0.1:8899`（撤销：`flatpak override --user --reset com.tencent.WeChat`）。

环境变量这条路**已经实测可行**：override 之后代理日志里出现了 `channels.weixin.qq.com` 的 TLS 握手尝试。注意桌面图标和托盘启动走的是 systemd app scope，终端里 `flatpak run --env=…` 那种一次性写法对它无效，所以要用 override（`scripts/run-wechat-proxied.sh --persist` 帮你写）。

## 3.4 让 webview 接受我们的证书（必须，只有这一条路）

路由通了以后 webview 依然弹「您的连接不是私密连接」，代理侧是 `Client TLS handshake failed … does not trust the proxy's certificate`。**这不是装 CA 能解决的** —— 三处信任库都装上、`--persist=.pki` 也确认沙箱里看得见 `cert9.db`，照样被拒，因为它的根证书编译在二进制里（证据见 `docs/project-background.md`）。

出路是给 webview 加 `--ignore-certificate-errors-spki-list=<base64(sha256(SPKI))>` —— 只放行我们这一个公钥，而不是整体关掉证书校验。它的命令行由 `libwmpf_host_export.so` 拼，不读环境变量也不读配置文件，所以我们在 exec 的那一刻把参数插进去：

```bash
scripts/build-appex-preload.sh            # 编译 LD_PRELOAD shim，并 --persist=.wx-dlh
scripts/run-wechat-proxied.sh --persist   # 代理环境变量 + LD_PRELOAD + 我们 CA 的 SPKI
# 然后把 WeChat 完全退出（托盘 → 退出）再启动
```

`--persist` 会写成 flatpak override，所以托盘和桌面图标启动一样生效。确认它真的起作用了：

```bash
# 参数进去了吗（应该看到 --ignore-certificate-errors-spki-list=...）
tr '\0' '\n' < /proc/$(pgrep -f 'WeChatAppEx --type=utility.*NetworkService' | head -1)/cmdline | grep spki
# 换个 CA 或重新生成 CA 之后要重跑 --persist，SPKI 会变
```

shim 的行为由 `tests/test_appex_preload.py` 守着（只对程序名 `WeChatAppEx` 的 exec 追加参数，已经在 argv 里的不重复追加，其他程序原样放过）。

## 3.5 先分清「视频播不出来」是谁的问题

**已经有答案了**：2026-09-11 重启 WeChat 后视频能正常播放。第一轮播不出来大概率是代理把主进程的 mmtls 长连接（`*.wechat.com`）也拦了 —— 那批 host 现在默认不拦截，大 body 也改成流式转发。下面这套判别留着，以后再出现播放问题时按它走：

1. 应用内代理**关掉**，播同一个视频 —— 还是播不出来？→ 与代理无关，很可能是 xrdp 环境（`WeChatAppEx` 的 gpu-process 跑的是 `--use-gl=angle --use-angle=swiftshader-webgl`，纯软件渲染），先解决这个再谈抓取。
2. 关掉代理能播、开着代理不能播 → 是代理干的。代理这一版已经做了两处针对性修正：`*.wechat.com` 的 mmtls host 默认不再拦截，大于 1 MB 的响应体直通不缓冲（`--stream-large-bodies`）。再复现一次，然后把 `captures/proxy.log` 给我。

## 4. 播一个视频号视频，然后看日志

盯三样东西：

```bash
tail -f captures/traffic-*.jsonl | grep -i channels     # H5 页面有没有请求
tail -f captures/captures-*.jsonl                       # 注入脚本有没有回传
jq -r 'select(.kind=="flow") | .host' captures/traffic-*.jsonl | sort | uniq -c | sort -rn
```

判定表：

| 现象 | 结论 / 下一步 |
|------|---------------|
| 终端出现 `TLS refused by the client` / webview 弹「您的连接不是私密连接」 | 客户端**已经在走代理**了，只是不认这张 CA。webview 的话看第 3.4 节（装 CA 没用，要那个 spki switch，并确认参数真的进了进程 cmdline）；主进程的话回到第 1 步，注意 `--persist=.pki` |
| `traffic-*.jsonl` 里完全没有 `channels.weixin.qq.com` | webview 没走代理 —— 第一轮就是这个，用第 3 节的环境变量重启；也可以直接看 WeChat 自己的记录确认它到底开了什么页面：`sqlite3 ~/.var/app/com.tencent.WeChat/.xwechat/radium/web/profiles/multitab_*/History 'select url from urls order by last_visit_time desc limit 5'` |
| 有 `channels.weixin.qq.com` 的 HTML + `injected: true` | 注入点成立，看 `captures-*.jsonl` 里的 `probe` |
| `probe` 的 `has_bridge` 为 `true`、随后有 `hook` | **注入路线在 Linux 上成立** |
| 只有 `invoke` 没有 `capture` | 记下这些 `err_msg`，就是 Linux 上那个未知字符串；媒体响应可能换了 API 名 |
| 出现 `capture` | M0 的关键一步拿到了：`decode_key` + `url` + `url_token` 都在日志里 |

### 代理里看不到 webview 流量时

- **QUIC / HTTP3**：Chromium 可能走 UDP，HTTP 代理接不到。日志里 host 很少但视频能播就是这个。WeChat 没有开关，只能从代理侧观察（`traffic-*.jsonl` 里 host 数量异常少）。
- **应用内代理只作用于主进程** —— 已实测确认，`WeChatAppEx` 不继承它。用第 3 节的 `scripts/run-wechat-proxied.sh` 重启整个应用。
- **mmtls**：WeChat 自己的传输协议不是 TLS。默认 `--ignore-host` 把 `*(long|short|minorshort|dns).(wechat.com|weixin.qq.com)` 排除在 MITM 之外（本机实际用的是 `sgshort.wechat.com`），登录流量原样穿过。想连它一起看：`--no-ignore-hosts`。

## 5. 收尾

M0 的结论（无论成立与否）写回 `docs/project-background.md` 的「待确认的前提」和 `docs/development-plan.md`，并在 issue #4 勾掉对应条目。真实的 JSAPI 响应形态脱敏后放进 `evals/`，特别是：

- Linux 上标记媒体响应的 `err_msg` 字符串；
- `decode_key` 在 JSON 里是字符串还是裸数字（日志里的 `decode_key_json_type` 直接给答案）；
- `media[0]` 是整段文件还是分片。
