# JSAPI 响应形态：Linux 实测（2026-09-11）

WeChat 4.0.1.11 Flatpak（`com.tencent.WeChat`），webview 是 `RadiumWMPF/runtime/WeChatAppEx`（Chromium 122.0.6261.156）。注入页面是 `https://channels.weixin.qq.com/web/pages/home?...`。样本为一次会话里的 7 个 `capture` 事件、共 62 条 `media[]`，**已脱敏**：这里只记字段名、类型与取值范围，不留真实的 `decode_key`、`url`、`url_token`、作者与描述。

## 1. 标记媒体响应的 `err_msg`

```text
api      = "finderH5ExtTransfer"      # 与 macOS 同名
err_msg  = "H5ExtTransfer:ok"         # 与 Windows 同串
```

**是个混血**，这也对得上 UA 里同时出现 `WindowsWechat(0x63090a13)` 和 `UnifiedPCLinuxWechat(0xf274010b)`。所以参考实现里 Windows 那条 `err_msg` 判定在 Linux 上可以直接用，但 api 名要按 macOS 的来。

同一次会话里其他 `invoke`（用来对照，说明筛选不能只看「有没有 err_msg」）：

| api | err_msg | 次数 |
|-----|---------|------|
| `finderH5ExtTransfer` | `H5ExtTransfer:ok` | 43 |
| `dispatchFinderAction` | `:ok` | 1 |
| `showMenuItems` | `:ok` | 1 |
| `hideMenuItems` | `system:function_not_implement` | 1 |

## 2. `decode_key` 在 JSON 里是字符串还是裸数字

**是带引号的字符串**（`decode_key_json_type` 全部为 `"string"`，62/62）。长度 **8–10 位**十进制，不是参考实现样本里那种 20 位的 64 位整数。

也就是说这批样本即便过一次 `JSON.parse` 也不会丢精度 —— 但**防护不能撤**：参考实现的样本是 20 位（`13835058055282163712`），一旦客户端换回裸数字形式就会静默出错。正则从响应原文里抠这条设计保持不变，`tools/check-inject.mjs` 和 `evals/README.md` 的黄金样本继续守着。keystream 黄金样本里的 `1234567890` 正好覆盖这次观测到的 10 位长度。

## 3. 整段文件还是分片

**整段文件**，参考实现的处理方式在 Linux 上成立：

- `media[]` 每条给一个 `download_url`（= `url` + `url_token`）和一个 `file_size`，没有 m3u8/m4s 之类的分片清单；
- 实测下载 index 11 那条：HTTP 200，`content-length` = 下载字节数 = `file_size` = 3873528，完全一致；
- 用 `decode_key` 生成 131072 字节 keystream，XOR 掉前 128 KiB，头 16 字节 `f8c4f724…` → `00000020 66747970 69736f6d 00000200`，即 `ftyp`/`isom` 的标准 mp4 头；
- `ffprobe`：h264 1080x2344、224 帧、aac、7.476 s；`ffmpeg -f null -` 全解一遍**零报错**，说明 128 KiB 边界之后的字节确实是原样保留的。

## 4. `media[]` 的字段（62/62 条都齐）

| 字段 | 类型 | 说明 |
|------|------|------|
| `decode_key` | string | 十进制，8–10 位；全程当字符串传 |
| `decode_key_json_type` | string | 本次全为 `"string"`，即响应原文里带引号 |
| `download_url` | string | `url` + `url_token` 拼好的直连地址 |
| `url` | string | 不含 token 的基础地址，host 为 `finder.video.qq.com` / `findera4.video.qq.com` |
| `url_token` | string | `&token=…&basedata=…&sign=…&svrbypass=…&svrnonce=…` |
| `file_size` | int | 与实际下载字节数一致 |
| `description` | string | 本次样本里为空串 |
| `nickname` | string | 本次样本里为空串 |

一个 `capture` 事件里 `media[]` 有多条（本次每次 8 条），**不是只有一条**：视频号 feed 页一次就把可见范围内的多个视频都带上了。参考实现只取 `media[0]`，对「当前这一个视频」够用，但面板要做列表的话这里本来就是多条。

`file_size` 跨度很大（3.8 MB – 585 MB），下载要流式 + 进度，不能整块读进内存。
