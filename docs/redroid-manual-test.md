# Redroid 手动测试（#27 / PR #29）

按 [owner 的补充要求](https://github.com/amyteq/at_wxclip/issues/27#issuecomment-6117679194) 提供。这份清单用于 owner 在本机桌面亲自检查；完整安装与失败证据见 [#30 的 spike runbook](https://github.com/amyteq/at_wxclip/issues/30)。本清单记录的 8.0.79 自动启动测试会闪退，所以先检查能否稳定打开，再决定是否登录；本文后半段是该轮尚未执行的条件步骤。下面的 `/path/to/at_wxclip` 是占位路径，使用前替换为自己的 checkout；容器名也应先与自己的隔离测试环境核对。

## 1. 启动已保留的平板

在宿主桌面的终端执行，保持这个终端可用：

```bash
cd /path/to/at_wxclip
docker ps -a --filter name=redroid-wx-dlh-27 --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

本机已经保留三个容器；它们共用 `127.0.0.1:5555`，一次只启动一个。默认与 tablet 容器还共用同一 Android data，不能同时运行。先停止本任务另外两个容器，再恢复 SM-X610 / Android 13 / WeChat 8.0.79：

```bash
docker stop redroid-wx-dlh-27 redroid-wx-dlh-27-compat
docker start redroid-wx-dlh-27-tablet
adb connect 127.0.0.1:5555
timeout 60 adb -s 127.0.0.1:5555 wait-for-device
adb -s 127.0.0.1:5555 shell getprop sys.boot_completed
```

最后一条必须返回 `1`；若为空，稍等后重跑该条。超过一分钟仍不是 `1`，先记录启动失败，不进入登录。确认属性、APK 和代理状态：

```bash
adb -s 127.0.0.1:5555 shell getprop ro.build.version.release
adb -s 127.0.0.1:5555 shell getprop ro.product.model
adb -s 127.0.0.1:5555 shell getprop ro.build.characteristics
adb -s 127.0.0.1:5555 shell wm size
adb -s 127.0.0.1:5555 shell wm density
adb -s 127.0.0.1:5555 shell dumpsys package com.tencent.mm | rg 'versionCode=|versionName='
adb -s 127.0.0.1:5555 shell settings put global http_proxy :0
adb -s 127.0.0.1:5555 shell settings get global http_proxy
```

预期为 Android `13`、`SM-X610`、`tablet`、`1600x2560`、`320`、`8.0.79` / `3200`、代理 `:0`。先关闭代理，避免尚未运行代理影响普通登录；设备属性只是输入，不能证明微信真的使用平板设备槽。

## 2. 打开可操作的屏幕

本次已从 [scrcpy 官方 v5.0.1 release](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1) 下载 Linux x86_64 包，解压在忽略目录 `temp/`。在同一个宿主桌面终端执行：

```bash
temp/scrcpy-linux-x86_64-v5.0.1/scrcpy \
  -s 127.0.0.1:5555 --no-audio --no-clipboard-autosync \
  --hwdec=disabled --render-driver=software \
  --window-title 'Issue 27 - WeChat tablet manual test' \
  --window-width 600 --window-height 960
```

本机已验证连接到 `samsung SM-X610 (Android 13)`，显示 `Renderer: software`、`Video decoding: software`、`Texture: 1600x2560`。本机桌面的 DRI3 提示未阻止软件渲染初始化。鼠标点击、滚轮和键盘可操作 Android；窗口需要在有图形会话的宿主桌面打开。此步骤没有自动操作微信登录。

若 `temp/` 已清理，可重新获取同一个官方版本：

```bash
mkdir -p temp
curl -fL --retry 2 --max-time 180 \
  https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-linux-x86_64-v5.0.1.tar.gz \
  -o temp/scrcpy-linux-x86_64-v5.0.1.tar.gz
tar -xzf temp/scrcpy-linux-x86_64-v5.0.1.tar.gz -C temp
```

## 3. 先验证微信能否打开

在平板窗口里手动点击微信，等待约 30 秒，观察是否仍保持同一个页面。仅短暂出现欢迎页、`am start` 返回成功、或短暂出现 PID 都不算稳定。若点图标没有反应，可在第二个终端执行：

```bash
cd /path/to/at_wxclip
adb -s 127.0.0.1:5555 shell am start -W -n com.tencent.mm/.ui.LauncherUI
adb -s 127.0.0.1:5555 shell pidof com.tencent.mm
```

若闪退或反复重启，到这里停止，不尝试扫码。在尚未登录的这次启动后，可将 crash buffer 保存到本地忽略目录：

```bash
adb -s 127.0.0.1:5555 logcat -b crash -d -t 140 > temp/redroid-manual-crash.txt
adb -s 127.0.0.1:5555 shell am force-stop com.tencent.mm
```

在 #27 只报告错误类型与必要的库名，例如 `Cannot process signal 11` / `libndk_translation.so` / `libowl.so`；不要直接上传整份日志。若需要复查 8.0.42，可停止 tablet 容器后启动 `redroid-wx-dlh-27-compat`，重新执行连接、关闭代理和屏幕步骤；它使用独立 data，既有实测是 ARM32 库加载失败，因此不是已知可用的替代方案。

## 4. 仅在不再闪退时，由 owner 登录验证

1. 先确认手机微信与原电脑微信都在线，保留电脑客户端用于观察。
2. 在平板窗口手动选择登录；若界面提供「平板登录」或扫码方式，记录所选方式。扫码、密码、手机确认均由 owner 自己完成，无需把二维码或凭据贴到任务中。
3. 登录后等待约一分钟，在手机「设置 → 账号与安全 → 登录设备管理」（实际名称以当前版本为准）查看新设备类别，记录是否明确识别为 Android 平板。
4. 回到原电脑客户端确认仍在线且可正常打开会话，手机也保持在线。若电脑或手机被挤下线，记录结果后停止本路线；不要为了继续测试反复切换登录。
5. 只有设备类别与共存均通过，才继续下一节。只看到 Android 的 `tablet` 属性，或者成功登录但未检查电脑，都不能算步骤①通过。

## 5. 仅在身份与共存通过后，手动播放一次

此节尚未实测。先在独立终端持续运行已有代理，流量只写入本机忽略目录：

```bash
cd /path/to/at_wxclip
WX_DLH_BRIDGE="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
WX_DLH_TEST_CA="${XDG_DATA_HOME:-$HOME/.local/share}/wx-dlh/redroid-27/ca"
test -n "$WX_DLH_BRIDGE" &&
  .venv/bin/python -m wx_dlh proxy --host "$WX_DLH_BRIDGE" --port 8899 \
  --ca-dir "$WX_DLH_TEST_CA" \
  --capture-dir temp/redroid-manual-captures > temp/redroid-manual-proxy.log 2>&1
```

第二个终端重新读取当前网桥地址，确认它与代理监听地址一致，再检查/安装 guest 的公钥 CA。这里沿用自己已生成的测试 CA，不复制其他机器的 CA，也不改动宿主信任库：

```bash
WX_DLH_BRIDGE="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
WX_DLH_TEST_CA="${XDG_DATA_HOME:-$HOME/.local/share}/wx-dlh/redroid-27/ca"
ss -ltn 'sport = :8899'
WX_DLH_CA_HASH="$(openssl x509 -in "$WX_DLH_TEST_CA/mitmproxy-ca-cert.pem" -subject_hash_old -noout)"
openssl x509 -in "$WX_DLH_TEST_CA/mitmproxy-ca-cert.pem" -fingerprint -sha256 -noout
```

核对 CA 指纹与自己代理使用的证书一致，且 `WX_DLH_CA_HASH` 是八位十六进制值；任一步失败就停止，不执行下方安装。若 CA 或网桥变化，按 [#30 runbook 第 3 节「代理与系统 CA」](https://github.com/amyteq/at_wxclip/issues/30#3-代理与系统-ca) 重新核对，不沿用其他机器的证书文件名或地址。

```bash
adb -s 127.0.0.1:5555 root
adb -s 127.0.0.1:5555 wait-for-device
WX_DLH_ANDROID_CA="/system/etc/security/cacerts/$WX_DLH_CA_HASH.0"
adb -s 127.0.0.1:5555 push "$WX_DLH_TEST_CA/mitmproxy-ca-cert.pem" "$WX_DLH_ANDROID_CA"
adb -s 127.0.0.1:5555 shell "chown 0:0 '$WX_DLH_ANDROID_CA' && chmod 644 '$WX_DLH_ANDROID_CA' && restorecon '$WX_DLH_ANDROID_CA'"
adb -s 127.0.0.1:5555 shell settings put global http_proxy "$WX_DLH_BRIDGE:8899"
adb -s 127.0.0.1:5555 shell settings get global http_proxy
```

如需重验 Android 的 CA 信任，完整重跑 #30 runbook 第 3 节的探针编译与执行；不要复用可能为 AVD 地址编译的旧 dex。`adb root` 若断开投屏，重新运行第 2 节。重新打开微信，在自己有权观看的视频号内容中只播放一个普通视频，记录开始/结束时间和能否播放；不做批量或无人值守播放。Android 默认 CA 通过不保证微信接受 CA，若出现网络或证书错误，应记录并停止。

在 #27 回报一次播放结果后，可据该时间窗口检查 media host、H5 页数、`probe.has_bridge` 事件数，以及必要时的 proxy-visible key 来源；只报告 host / content-type / 布尔值 / 计数，不上传含查询参数的完整 URL、key 值、原始流量、页面或媒体。未播放成功或未确认身份时，不能将零事件写成「无 H5/bridge/key」。

## 6. 结束与回报

先关闭 Android 代理，再关闭投屏（窗口关闭或终端 Ctrl+C）、在代理终端 Ctrl+C，最后停止当前容器。停止保留 data 与登录状态，不执行清空 data 或卸载：

```bash
adb -s 127.0.0.1:5555 shell settings put global http_proxy :0
adb -s 127.0.0.1:5555 shell am force-stop com.tencent.mm
docker stop redroid-wx-dlh-27-tablet
adb disconnect 127.0.0.1:5555
```

若测试的是 compat，最后停止 `redroid-wx-dlh-27-compat`。下面的简短模板可贴到 #27，不需要截图或账号信息：

```text
测试时间 / 时区：
容器 / Android / 微信版本：
能否稳定打开 30 秒：是 / 否（错误类型或库名）
登录方式：未到此步骤 / 平板扫码 / 其他
手机显示的设备类别：未验证 / Android 平板 / 其他
手机与原电脑是否仍在线：未验证 / 均在线 / 哪一类被退出
代理与播放：未到此步骤 / 成功播放一次 / 具体错误
播放时间窗口（若有）：
已关闭代理并停止容器：是 / 否
```
