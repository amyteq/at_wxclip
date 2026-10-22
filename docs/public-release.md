# 公开源码前的检查

本仓库可作为个人使用的 Python 工程示例。公开范围不包括捕获的媒体、聊天截图、流量、URL token、decode_key、CA 私钥、设备签名包或机器配置；源码公开也不表示未完成的平台路线或模型评估已经通过。

## 源码与历史

- 从待发布的准确提交运行 `make lint test`，检查最终 diff 和打包内容。
- 当前文件与可达 Git 历史分别检查凭据、私钥、实际宿主路径、私有网络配置和捕获产物；当前文件的删除或替换不清除历史。
- issues、PR、reviews、discussions、评论、附件、Actions 日志和制品是独立的公开面，不能用源码扫描替代。未审查的附件不视为安全。
- 文档使用占位路径或运行时读取的地址，CA 指纹和设备标识留在本机。作者姓名/邮箱等有意公开的项目元数据与认证凭据分别处理。
- 不把 `.env`、`captures/`、`downloads/`、`processed/`、`temp/` 或被忽略的签名材料强制加入 Git。

## 外部互动与自动化

`.github/workflows/ci.yml` 只有 `workflow_dispatch`，使用只读 `contents` 权限，checkout 不保存凭据。没有 push、PR、issue、comment 或提及触发入口；本地验证仍用 `make lint test`。此文件只限制仓库中的 CI，不能控制 GitHub App 或其他外部订阅。

公开前由维护者在 GitHub 实际核实：

1. 关闭不接受的 Issues、Discussions 等互动入口，并确认 PR 的实际限制能力；不要把关闭 Issues 或禁用 Actions 当作关闭 PR。
2. 要求外部活动完全不启动自动化时，禁用 Actions，并从 Claude、Codex 及其他会处理事件的 App 中移除此仓库；核查仓库 webhooks、外部订阅及定时任务。
3. 不给公开仓库接入自托管 runner，不向外部贡献流程传递 secrets 或写 token。
4. 如果以后只允许维护者手动检查，可重新评估 Actions 设置后使用现有 manual workflow；不要恢复外部事件触发而仍声称“外部活动零触发”。

这些设置需要在实际仓库上读回验证，本文件不证明它们已配置。PyPI、GitHub Release 与 donation 是单独的发布工作，不随源码公开自动执行。
