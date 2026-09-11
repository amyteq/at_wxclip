# scripts

给人手动执行的入口脚本，一个脚本干一件事，保持可读。

- `setup.sh` —— 创建 `.venv` 并安装依赖（`make setup` 调用它）
- `trust-ca.sh` —— 把代理 CA 装进 WeChat 真正会读的信任库（Flatpak 沙箱 NSS / 用户 NSS /
  系统信任库），`--list` 查看、`--remove` 撤销
- `run-wechat-proxied.sh` —— 带 `http_proxy` 环境变量启动 WeChat Flatpak，让**webview** 也走
  代理（应用内那个设置只管主进程，见 `docs/M0-操作步骤.md`）

需要被 Python 代码复用的逻辑放进 `wxclip/`，不要留在脚本里。
