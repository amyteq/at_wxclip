# scripts

给人手动执行的入口脚本，一个脚本干一件事，保持可读。

- `setup.sh` —— 创建 `.venv` 并安装依赖（`make setup` 调用它）
- `trust-ca.sh` —— 把代理 CA 装进 WeChat 真正会读的信任库（Flatpak 沙箱 NSS / 用户 NSS / 系统信任库），`--list` 查看、`--remove` 撤销
- `run-wechat-proxied.sh` —— 带 `http_proxy` 环境变量启动 WeChat Flatpak，让**webview** 也走代理（应用内那个设置只管主进程，见 `docs/M0-runbook.md`）；同时装上下面那个 shim，并把我们 CA 的 SPKI 传给它。`--persist` 写成 flatpak override，托盘/桌面图标启动也生效
- `build-appex-preload.sh` + `appex-flags-preload.c` —— LD_PRELOAD shim，在 exec 的那一刻给 webview（`WeChatAppEx`）追加 `--ignore-certificate-errors-spki-list=<我们的 CA>`。**webview 的根证书编译在它自己的二进制里，装 CA 对它无效**，这是唯一的入口；只放行我们这一个公钥，不是整体关掉证书校验（见 `docs/project-background.md`）

需要被 Python 代码复用的逻辑放进 `wxclip/`，不要留在脚本里。
