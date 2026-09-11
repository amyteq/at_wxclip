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

**M1 已经把 ISAAC64 移植进 `wxclip/keystream.py`，这张表就是它的验收标准**：
`tests/test_keystream.py` 每次跑都比对这五行，五行全中才算移植正确。重新生成或加新种子：

```bash
node tools/keystream-golden.mjs --decrypt-js <path>/electron/decrypt.js
node tools/keystream-golden.mjs --seed 4145908544 --decrypt-js <path>/electron/decrypt.js
```

这个脚本顺带用同一个种子调一次 Python 版并对比，不一致就非零退出。

移植时对上这张表的关键点（当初逐条试出来的）：seed 放进结果数组第 0 槽、`randinit` 走带
seed 的那条路径（两遍 mix）、取值顺序是 `rand()` 的顺序（结果数组从末尾往前）、每个 64 位字
按**大端**写出。`decrypt.js` 末尾那个 `reverse()` 是它自己内部布局的产物，不要照抄。

| seed（字符串） | 长度 | keystream 的 sha256 |
|----------------|------|---------------------|
| `0` | 131072 | `e1662af3b7e59867c919ad19055fc8cecea2b154d37e1459b2f96d1da14cef1f` |
| `1` | 131072 | `39d98f5b25cc52f0996f7ef9e1022156cb029901be6a11ebbdd7469c6ffd5839` |
| `1234567890` | 131072 | `62c16e2c25857dfef81231038222c63d5fef01f03b9b74779475a5576e051efb` |
| `13835058055282163712` | 131072 | `da37f3ac8f8d2699aded0c966d7cff55e2b63be2798db981c740d0d7a9fd5195` |
| `18446744073709551615` | 131072 | `5afbffd76305e81467f97c6370fa07916e9b197611bf5c357a854eb92a6354a1` |

最后两行是刻意选的：`13835058055282163712` 一旦被当成 JS number 解析、再打印回字符串就变成
`13835058055282164000`（double 存得下这个值，丢的是十进制写法），keystream 完全不同 ——
它的 sha256 是 `3b137d5456a342a010edf0e1a113652f0cf63165afea4dcff8fab3ff935986ea`，
`tests/test_keystream.py` 也钉住了这一条；`18446744073709551615` 是 uint64 上界。
两者都能把「精度丢了」这类错误当场照出来。`1234567890` 覆盖 Linux 实测的 10 位长度。
