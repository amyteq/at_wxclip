# evals

固定样本 + 校验脚本，用来回答两个问题：

1. **解密是否仍然有效** —— WeChat 更新后 JSAPI 响应的字段形态会变，这里放脱敏后的
   JSAPI 响应样本；以及 keystream 的黄金样本：`decode_key`（十进制字符串）→ 131072 字节
   keystream 的 sha256，用来验证 Python 移植版与 `decrypt.js` 输出一致；
2. **提取/总结质量是否退化** —— 截屏、文字提取、总结的 prompt 变更前后对比。

改动 keystream 实现、JSAPI 解析或 prompt 之前，先在这里补一个有代表性的样本。

已有的样本：

- `jsapi-response-linux-20260911.md` —— Linux 上真实 JSAPI 响应的脱敏形态：`err_msg` 是
  `H5ExtTransfer:ok`（api 名 `finderH5ExtTransfer`）、`decode_key` 是带引号的 8–10 位十进制
  字符串、媒体是整段文件而非分片。只记字段名/类型/取值范围，不留真实 key 与地址。

体积大的视频/音频样本不进 Git：放外部网盘，并在本文件登记来源、大小与 `sha1sum`。

| 样本 | 来源 | 大小 | sha1 | 用途 |
|------|------|------|------|------|
| _待补_ | | | | |

## keystream 黄金样本

用 `harrysun2006/WeChatVideoDownloader` 的 `electron/decrypt.js`（v2.0.1）在纯 Node
v24 下生成：`getDecryptionArray(seed)`，seed 以**十进制字符串**传入，输出 131072 字节。
移植 ISAAC64 到 Python 时用这张表验证；对不上就是移植错了（注意末尾的 `reverse()`）。

| seed（字符串） | 长度 | keystream 的 sha256 |
|----------------|------|---------------------|
| `0` | 131072 | `e1662af3b7e59867c919ad19055fc8cecea2b154d37e1459b2f96d1da14cef1f` |
| `1` | 131072 | `39d98f5b25cc52f0996f7ef9e1022156cb029901be6a11ebbdd7469c6ffd5839` |
| `1234567890` | 131072 | `62c16e2c25857dfef81231038222c63d5fef01f03b9b74779475a5576e051efb` |
| `13835058055282163712` | 131072 | `da37f3ac8f8d2699aded0c966d7cff55e2b63be2798db981c740d0d7a9fd5195` |
| `18446744073709551615` | 131072 | `5afbffd76305e81467f97c6370fa07916e9b197611bf5c357a854eb92a6354a1` |

最后两行是刻意选的：`13835058055282163712` 一旦被当成 JS number / float 解析会变成
`13835058055282164000`，keystream 完全不同（`3b137d54…`）；`18446744073709551615` 是
uint64 上界。两者都能把「精度丢了」这类错误当场照出来。
