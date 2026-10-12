# 跨平台验证手册（Windows / macOS）

对应 issue [#11](https://github.com/amyteq/at_wxclip/issues/11)。

`wx-dlh` 里会动字节的部分已经是跨平台的：`keystream`、`decrypt`、`downloader`、`pipeline`、`server`、`web/inject.js`，以及代理本身。**只有两件事不是**：

| | 问题 | Linux 上的答案 |
|---|---|---|
| 路由 | 怎么让本机 WeChat 的流量到达我们的代理 | 应用内代理设置（主进程）+ `flatpak override --env=http_proxy=…`（webview） |
| 信任 | 怎么让它接受我们这张 CA | NSS 库（主进程）+ `--ignore-certificate-errors-spki-list`（webview，装 CA 无效） |

这份文档不是「Windows/macOS 怎么做」的说明书 —— 那份说明书还不存在，因为没人在那两个平台上跑过。它是**去跑的人手里那张清单**：先验什么、后验什么、每一步什么算过、以及哪几步会「命令全成功但其实没生效」。

## 为什么是清单先于代码

M0 在 Linux 上的教训：`update-ca-trust` 成功、`certutil` 成功、证书确实躺在那个路径上，客户端照样报 `tls alert certificate unknown`。真正的原因要到进程的 `/proc/<pid>/maps` 里才照得出来（Flatpak 沙箱不挂载 `$HOME/.pki`；webview 的根证书编译在 `WeChatAppEx` 二进制里）。

**每一条命令的退出码都是 0，而整件事是坏的。** 照着参考实现抄一份 Windows/macOS adapter 出来，得到的就是这种东西：能跑、不报错、没用，而且下一个人会以为它是对的、从它开始往下查。所以 adapter 等实测，清单先写 —— 清单里每一条的判定都落在**客户端侧的证据**上，不落在命令的退出码上。

## 四道关，按顺序验，不要合并

四道关正好对应 `wx-dlh proxy` 已经在写的日志，所以不需要额外工具，跑起来看文件就行。**一次只推进一关**：合并着验，失败时分不清是哪一层坏的（Linux 第一轮就是这样白烧了一轮）。

| # | 关 | 怎么看 | 什么算过 |
|---|---|--------|----------|
| 1 | **路由** | `captures/traffic-*.jsonl` | 出现 `channels.weixin.qq.com` 的条目。只看到头像、mmtls 那些 host = 主进程进来了、webview 没进来 |
| 2 | **信任** | 同上 + `captures/pages/*.html` | 那条不是 TLS 握手失败，且 `pages/` 下真的落了 HTML 文件（= 拿到了明文） |
| 3 | **注入** | `captures/captures-*.jsonl` | 有 `kind` 为 `hook` 的事件，且它的 `payload.has_invoke` 是 `true`（= `WeixinJSBridge.invoke` 真的被包上了）。别拿 `probe` 判这一关，见纪律 4 |
| 4 | **抓取** | 同上 | 有 `capture` 事件，带 `decode_key` |

四关都过之后端到端收尾：

```bash
wx-dlh download --from-capture --index 0     # 直连下载 + 解密
ffprobe <file>                               # 认得出 h264/aac 就是真的过了
```

### 四条判定纪律

1. **命令成功不算过。** 每一关都要有客户端侧的证据（上表右列），不要拿命令退出码交差。
2. **改完信任库要重启 WeChat。** 证书库是启动时读的；不重启就看到的失败是假失败。
3. **别信标记文件。** 参考实现装完证书写一个 `installed.lock`，之后只查这个文件在不在（`cert.js` 的 `checkCertInstalled`），**从不回头查证书是不是真在库里**。人手把证书删了、或者当初压根没装进去，它都认为装好了。我们要落地的话，装完必须回查一次真库 —— `wx-dlh ca --install`（#28）和它的 shell 版 `scripts/trust-ca.sh`（#23）都是这么做的。但库里查得到只说明装上了，不说明 WeChat 认它，那是第 2 关要回答的。
4. **`probe` 不是第 3 关的判据。** `probe` 只在注入脚本装完钩子那一刻发一次（`web/inject.js` 末尾的 `install()` 之后），里面的 `has_bridge` / `hooked` 记的是**那一刻**的状态。客户端晚一步才给 `window.WeixinJSBridge` 赋值的话，这唯一一条 `probe` 两个字段都是 `false`，之后真包上了也只补一条 `hook` 事件、不会再发一条 `probe` —— 照着 `probe` 判就记下一个假失败。反过来，`hook()` 里 `state.hooked` 是无条件置 `true` 的（`invoke` 不是函数时也照置，只是不包），所以 `hooked: true` 也不等于 invoke 包上了。两头都能答的只有 `hook` 事件里的 `has_invoke`。`probe` 仍然要看，但它回答的是另一个问题：这一帧里脚本到底跑没跑（连一条 `probe` 都没有 = 注入没落地，回头看第 2 关，别在第 3 关上耗）。

## Windows

### 参考实现做什么（2026-09-13 逐行核对）

读的是 `harrysun2006/WeChatVideoDownloader` master 分支的 `electron/{setProxy,cert,const,proxyServer}.js`。

**代理设置**（`setProxy.js`）—— 通过 npm 包 `regedit`（走 VBS，`REGEDIT_VBS_PATH`）写注册表：

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings
    ProxyServer   REG_SZ      <host>:<port>
    ProxyEnable   REG_DWORD   1
```

撤销时**只把 `ProxyEnable` 写 0**，`ProxyServer` 留在原地。没有写 `ProxyOverride`，也**没有调 `InternetSetOption(INTERNET_OPTION_SETTINGS_CHANGED)`** 去通知已经跑着的进程。

注意这条分支是 `else`：`process.platform === 'darwin'` 之外一律写注册表，所以在 Linux 上跑它会去写注册表 ——「参考实现没有 Linux 分支」说的就是这个。

**证书安装**（`cert.js`）—— `spawn.sync(public/w_c.exe, ['-c', '-add', <cert>, '-s', 'root'])`，判定是 stdout 里出现 `Succeeded`。这组参数是 `certmgr.exe` 的形状，`-s root` 指**当前用户**的 Root 存储（不是 `-s -r localMachine root`），所以不需要管理员权限。成功后 `touch` `~/.wechat-video-downloader/installed.lock`。

**另外**：`proxyServer.js` 在 `win32` 上把 `OPENSSL_BIN` / `OPENSSL_CONF` 指向自带的 `public/openssl/openssl.exe`。那是 `hoxy` 现生成 CA 用的，我们用 mitmproxy 自己管 CA，这条不需要。

### 待回答的问题

按这个顺序问，因为后面的问题只有前面答了才有意义：

1. **WeChat 主进程走不走 WinINET？** 写了那两个注册表值之后，主进程的请求到没到代理（第 1 关）。如果 WeChat 用的是 WinHTTP 或自己的网络栈，`ProxyEnable` 对它就是个摆设。
2. **不重启客户端生效吗？** 参考实现不发 `INTERNET_OPTION_SETTINGS_CHANGED`，已经缓存了设置的进程可能要重启才认。先试不重启，不行再重启 —— 结论直接决定 adapter 要不要发这个通知。
3. **视频号 webview 是什么内核、走不走系统代理？** Linux 上它是自带的 Chromium 122，**应用内代理设置管不到它**，只能靠环境变量。Windows 上是 XWeb / RadiumWMPF 还是系统 WebView2，它吃不吃 WinINET 设置 —— 这是整件事最可能卡住的地方。
4. **webview 查不查系统信任库？** Linux 上**不查**（根证书编译在二进制里），所以只能用 `--ignore-certificate-errors-spki-list`。参考实现在 Windows 上能成，说明那里的内核大概率查系统库 ——**但这是推断，不是实测，必须在机器上确认**。不成立的话就要找 Windows 上等价的「给 webview 加 Chromium switch」的办法，LD_PRELOAD 那招在 Windows 上不存在。
5. **mmtls 的 host 是哪一半？** Linux 上是 `*.wechat.com`，参考实现按的是 `*.weixin.qq.com`。代理默认不 MITM `(long|short|minorshort|dns).(wechat.com|weixin.qq.com)`，两边都盖住了，但 `captures/traffic-*.jsonl` 里 host 异常稀少的话要回头看这条（`--ignore-host` 可改）。
6. **`err_msg` 与 `decode_key` 的形态。** 参考实现的注释说 Windows 是 `H5ExtTransfer:ok`；Linux 实测是「api 名同 macOS（`finderH5ExtTransfer`）、`err_msg` 同 Windows」的混血。`decode_key` 在 Linux 上是**带引号、8–10 位**，参考实现样本里是 20 位。两样都记下来，脱敏后进 `evals/`。

### 步骤

```powershell
python -m wx_dlh ca            # 生成 CA，打印路径与指纹
python -m wx_dlh proxy         # 另开一个终端，让它一直跑
```

然后按上面「四道关」逐关推进：设注册表 → 看第 1 关 → 装证书（`wx-dlh ca --install`，在 PowerShell / cmd 或 WSL 里跑都行，默认进 `CurrentUser\Root`；本机没装 wx-dlh 就在 Git Bash / MSYS2 / Cygwin 或 WSL 里跑 `scripts/trust-ca.sh`，参数相同）+ 重启 WeChat → 看第 2 关 → 播一个视频号视频 → 看第 3、4 关。每一关的结论填进本文末尾的登记表。

**在 WSL 里跑的也算 Windows。** `sys.platform` 是 linux，但 WeChat、要写的注册表、要装的证书库都在宿主机上，所以 `platforms.current()` 直接取 `trust.detect_os()` 的答案（`wx_dlh/platforms.py`）：状态行写 `Windows host (from WSL)`，`wx-dlh proxy` / `capture` 一样提示没验证过。四道关的证据仍然看 WSL 里代理写出来的 `captures/`，但每一关要动的东西都在宿主机那边。

## macOS

### 参考实现做什么（同日核对）

**代理设置**（`setProxy.js`）：

1. `networksetup -listallnetworkservices` 列出所有网络服务；
2. 逐个用 `networksetup getinfo "<服务名>" | grep "^IP address:\s\d"` 筛掉没有 IP 的；
3. 对筛出来的每一个：`networksetup -setsecurewebproxy "<服务名>" <host> <port>`。

撤销是 `networksetup -setsecurewebproxystate "<服务名>" off`。

**两处值得先盯一眼**：

- **只设了 `-setsecurewebproxy`（HTTPS），没设 `-setwebproxy`（HTTP）。** 视频号页面是 HTTPS，所以够用；但 `wx-dlh` 的 `/__wx-dlh/*` 也走同源 HTTPS，一致，问题不大 —— 记一笔，别当成遗漏改掉。
- **那条 `grep` 用的是 `\s` 和 `\d`。** `\d` 不是 GNU grep 也不是 BSD grep 的 BRE 语法（那是 PCRE，要 `-P`）。如果它匹配不到，筛选结果为空 → `networks.length === 0` → `setProxy` 直接 `throw 'no network'`。**上机第一件事就是验这条**：

  ```bash
  networksetup -listallnetworkservices
  networksetup getinfo "Wi-Fi" | grep "^IP address:\s\d" ; echo "exit=$?"
  ```

  `exit` 非 0 就说明这个筛子在这台机器上什么都筛不出来，照抄必坏。换成可移植的写法（`grep -E '^IP address: [0-9]'`）再往下走。

**证书安装**（`cert.js`）—— **它不装**。macOS 分支把一条命令写进剪贴板、弹个窗让人自己粘到终端跑，然后函数 `reject()`：

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain "<证书路径>"
```

`-d` 加到 admin domain、`-k` 指系统钥匙串、`-r trustRoot` 作为根证书信任，要 sudo。参考实现选择让人手动跑，多半就是因为这一步要密码 —— 我们要落地的话也别想着替用户输密码，把命令打出来、装完回查一次更诚实。

### 待回答的问题

1. **系统代理对 WeChat 主进程生效吗？**（第 1 关）macOS 的 `networksetup` 设的是系统级 HTTP 代理，一般 CFNetwork 的应用都认，但 WeChat 自己的网络栈认不认要实测。
2. **视频号 webview 走不走系统代理？** 这是 Linux 上翻车的那一条，macOS 上必须单独确认 —— 别因为「主进程进来了」就以为 webview 也进来了。看第 1 关里有没有 `channels.weixin.qq.com`，不是有没有流量。
3. **webview 查不查系统钥匙串？** 同 Windows 第 4 问。查 → `add-trusted-cert` 就够了；不查 → 要找 macOS 上给 webview 加 Chromium switch 的办法。
4. **`err_msg` 与 `decode_key` 的形态。** 参考实现的注释说 macOS 是 `finderH5ExtTransfer:ok`。实测确认，脱敏样本进 `evals/`。
5. **Apple Silicon / Intel 有没有差别**，以及 WeChat 版本号 —— 一并记下来，这类结论会随版本漂。

### 步骤

```bash
python -m wx_dlh ca
python -m wx_dlh proxy
```

同样按四道关推进：`networksetup -setsecurewebproxy` → 第 1 关 → `wx-dlh ca --install`（默认登录钥匙串；`--system` 是参考实现用的系统钥匙串；本机没装 wx-dlh 就用 `scripts/trust-ca.sh`，参数相同）+ 重启 WeChat → 第 2 关 → 播视频 → 第 3、4 关。

## 验完之后往代码里落什么

顺序是**先改事实，再写代码**：

1. **`docs/project-background.md`** —— 把结论写进对应平台那一节（已经开好位置，和 Linux 那节并列）。这是最重要的一步：协议与客户端行为的结论是这个项目最贵的资产，代码是它的附属品。
2. **`wx_dlh/platforms.py`** —— 把该平台的 `HostSupport` 改成 `verified=True`，`routing` / `trust` 换成**实测到的**做法（现在 `routing` 写的是参考实现的做法，措辞明确标着「never run for this project」；`trust` 指向 `wx-dlh ca --install`，标着「never watched against a real WeChat」）。
3. **adapter 本身** —— 到这一步才写，而且照着实测出来的路径写，不是照着参考实现写。信任那一半已经是 `wx_dlh/trust.py`（`wx-dlh ca --install`）和它的 shell 版 `scripts/trust-ca.sh`，实测不对就两边一起改，不另起一份；还没有的是路由。三条约束：
   - **可撤销**：设了代理就要有 `--unset`，而且要把值真的清干净（参考实现留着 `ProxyServer` 只清 `ProxyEnable`，算半个撤销）。
   - **装完回查**：不要学 `installed.lock`。装完去真库里查一次，查不到就报错。
   - **不静默**：每一步打印它实际执行的命令和结果，因为下一个在别的版本上踩坑的人只有这些输出可看。
4. **`docs/M0-runbook.md` 的同级文档** —— 该平台的操作步骤，按实际跑通的顺序写。
5. **本文件** —— 把该平台从「待验」挪到「已验」，问题清单换成结论。

## 结论登记

实测之后填这张表，并把详细结论写进 `docs/project-background.md`。

| | Windows | macOS |
|---|---|---|
| 验证日期 / 验证人 | | |
| WeChat 版本 | | |
| OS 版本（含架构） | | |
| 1 路由：主进程 | | |
| 1 路由：webview | | |
| 2 信任：主进程 | | |
| 2 信任：webview | | |
| 3 注入：`hook` 事件的 `has_invoke` | | |
| 4 `decode_key` 到手 | | |
| 端到端可播放文件 | | |
| webview 内核与版本 | | |
| webview 查不查系统信任库 | | |
| mmtls host 是哪一半 | | |
| api 名 / `err_msg` | | |
| `decode_key` 位数 / 带不带引号 | | |

## 参考

- issue #11：https://github.com/amyteq/at_wxclip/issues/11
- Linux 侧的完整结论：`docs/project-background.md`、`docs/M0-runbook.md`
- 参考实现：https://github.com/harrysun2006/WeChatVideoDownloader（`electron/setProxy.js`、`electron/cert.js`、`electron/const.js`）
