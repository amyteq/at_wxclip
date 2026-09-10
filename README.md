# at_wxclip

把 Linux 版 WeChat 里能播放的视频，抓到真实地址并直接下载下来，再做截屏、文字提取和
总结。一个 headless 命令行工具 + 一个轻量 web server 作为 GUI。

## 工作原理

```text
WeChat for Linux ──(HTTP/HTTPS 走本地代理)──> wxclip proxy ──> 记录候选视频请求
                                                    │
                                                    ▼
                                          decoder（JS 解密逻辑）
                                                    │
                                                    ▼
                                    真实下载地址 ──> 直接下载（不走代理）
                                                    │
                                                    ▼
                                     截屏 / OCR 文字提取 / 内容总结
```

1. 启动本地代理服务器，并在 WeChat for Linux 中把它设置为 HTTP 代理；
2. 在 WeChat 里播放目标视频，代理即可捕获到请求地址；
3. 交给 decoder 解析出真实下载地址（参考 `WeChatVideoDownloader` 的 `decrypt.js`）；
4. 由本工具直接下载视频，下载不经过代理；
5. 视频可进一步做截屏、文字提取、总结等操作。

## 技术栈

- **Python**：代理服务器（抓取目标链接）、下载器、以及提供管理与下载面板的 web server
- **JavaScript / TypeScript**：前端页面，以及解析视频真实地址的解密逻辑
- 假设 WeChat 运行在同一台 Linux 机器上

## 快速开始

```bash
make setup          # 创建 .venv 并安装依赖（uv，回落到 pip）
make lint test      # 检查 + 测试
python -m wxclip --help
```

## 目录

| 路径       | 说明                                             |
|------------|--------------------------------------------------|
| `wxclip/`  | Python 包：proxy / decoder bridge / downloader / server |
| `web/`     | 前端页面与 JS 解密逻辑                            |
| `docs/`    | 背景、开发计划、规格说明                          |
| `tests/`   | pytest 测试                                       |
| `scripts/` | 给人手动执行的入口脚本                            |
| `tools/`   | 辅助小工具，不属于交付包                          |
| `evals/`   | 解码/提取/总结质量的固定样本与校验                 |

详见 `docs/目录说明.md`；智能体协作约定见 `AGENTS.md`。

## 参考

- https://github.com/harrysun2006/WeChatVideoDownloader
- 项目介绍讨论：https://github.com/amyteq/at_wxclip/discussions/2

## 说明

仅用于下载本人 WeChat 客户端中已可正常播放的内容，供个人使用。不做付费/受限内容的
DRM 破解，不做他人账号的批量抓取，不再分发抓取到的媒体文件。
