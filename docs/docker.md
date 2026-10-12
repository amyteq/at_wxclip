# 用 Docker 运行 wx-dlh

把 Python 环境、mitmproxy、代理（`wx-dlh proxy`）、一次性抓取（`wx-dlh capture`）和面板（`wx-dlh serve`）都放进容器。镜像基于 `python:3.12.12-slim-trixie`（Debian 13），文件是仓库根目录的 `Dockerfile`、`compose.yaml`、`.dockerignore` 和 `scripts/docker-entrypoint.sh`。

## 能省什么，省不掉什么

先说结论：**容器省掉的是宿主机上的 Python 环境和各系统的启动方式，省不掉 CA 信任和 WeChat 的代理设置。** 原因是「信不信这张证书」「走不走代理」都是宿主机上 WeChat 进程自己做的决定，读的是宿主机的信任库和 WeChat 自己的设置，容器碰不到它们。

| 事项 | 原生安装 | Docker |
|------|---------|--------|
| Python 3.11+ / uv / micromamba、mitmproxy 依赖 | 要装 | 在镜像里 |
| 起代理、面板、capture | 各系统各自的 shell | `docker compose up -d` |
| Windows：代理跑在 WSL 里时的 NAT / mirrored 网络 | 要处理 | 不用：Docker Desktop 把端口直接发布到 Windows 的 `127.0.0.1` |
| CA 证书信任 | 要做 | **仍然要做**，在宿主机上，每台机器一次 |
| WeChat 指向代理 | 要做 | **仍然要做** |
| Linux：webview 的 SPKI shim（`run-wechat-proxied.sh --persist`） | 要做 | **仍然要做** |

容器做到的是让这两步只做一次：CA 由容器生成，但存在宿主机的 `~/.config/wx-dlh/ca`（和原生安装同一个目录），重建、升级镜像都不会换 CA，信任过一次就一直有效。

## 准备

- macOS / Windows：[Docker Desktop](https://docs.docker.com/desktop/)。Windows 用 WSL 2 后端，`docker compose` 在 PowerShell 或 WSL 里跑都行。
- Linux：[Docker Engine](https://docs.docker.com/engine/install/) + [Compose 插件](https://docs.docker.com/compose/install/linux/)。
- Compose v2（`docker compose`，不是旧的 `docker-compose`）。不需要 BuildKit / buildx：`Dockerfile` 用的都是旧构建器也认的语法，Podman 也能构建。

## 快速开始

```bash
# Linux 上先把要挂载的目录建好，不然 Docker 会以 root 身份创建它们（容器会接管最内层那个，但父目录留成 root 的）
mkdir -p ~/.config/wx-dlh/ca captures downloads processed

docker compose up -d --build        # 代理 127.0.0.1:8899，面板 http://127.0.0.1:8080
docker compose logs proxy           # 看到 "wx-dlh proxy listening on http://0.0.0.0:8899" 即可
```

第一次启动时容器生成 CA，宿主机上就有了 `~/.config/wx-dlh/ca/mitmproxy-ca-cert.pem`。接下来在宿主机上做两步。

### 1. 信任 CA（每台机器一次）

```bash
scripts/trust-ca.sh                 # 默认就读 ~/.config/wx-dlh/ca/mitmproxy-ca-cert.pem
```

- macOS：装进登录钥匙串（会弹密码框）。
- Windows：在 Git Bash 或 WSL 里跑，装进 `CurrentUser\Root`（会弹确认框）。不想装 Git Bash 的话，PowerShell 里 `certutil -user -addstore Root "$env:USERPROFILE\.config\wx-dlh\ca\mitmproxy-ca-cert.pem"` 是同一件事。注意 `~` 是各自环境的家目录：在 PowerShell 里起的 `docker compose`，CA 在 `C:\Users\<你>\.config\wx-dlh\ca`，这时在 WSL 里跑脚本要加 `--cert /mnt/c/Users/<你>/.config/wx-dlh/ca/mitmproxy-ca-cert.pem`；在同一个环境里跑就不用。
- Linux：装进 Flatpak 沙箱的 NSS 库。但 webview 根本不看信任库（见 `docs/M0-runbook.md`），所以还要 `scripts/run-wechat-proxied.sh --persist`：它把这张 CA 的公钥指纹交给 webview，并让 webview 也走代理。代理在容器里也一样，脚本只看 `127.0.0.1:8899` 有没有人在听。

各系统的状态见 README 的表格：macOS / Windows 的信任路径还**没有在真实 WeChat 上验证过**（#11）。

### 2. 让 WeChat 走代理

WeChat 设置 → 通用设置 → 网络代理 → HTTP，`127.0.0.1`，端口 `8899`。Linux 上再加上一步的 `run-wechat-proxied.sh --persist`。

### 3. 播放、下载

在 WeChat 里打开视频号播放，`docker compose logs -f proxy` 能看到 `injected hook script` 和带 `decode_key` 的 `[invoke]` 行；打开 http://127.0.0.1:8080 点下载，文件落在仓库下的 `downloads/`。

```bash
docker compose down                 # 用完就停；WeChat 里的代理设置记得一起关
```

## 一次性抓取（capture）

和原生的 `wx-dlh capture` 一样：起代理、抓到就下载、然后退出。它要占用代理端口，所以先停掉常驻的那两个：

```bash
docker compose stop
docker compose run --rm --service-ports capture --count 1
docker compose run --rm --service-ports capture --count 3 --max-size 200m --wait 120
```

## 其他命令

`cli` 服务挂的是同一组目录，后面接任意子命令：

```bash
docker compose run --rm cli download --list
docker compose run --rm cli download --from-capture --index 2
docker compose run --rm cli --help
```

截屏、转写这些后处理要 ffmpeg 和 tesseract，镜像默认不带（多几百 MB）。需要的话用 `WX_DLH_MEDIA_TOOLS=1 docker compose build` 重建。

## 配置

都是可选的，写在 shell 环境里或 `compose.yaml` 旁边的 `.env` 里（`.env` 不进 Git）。

| 变量 | 默认 | 作用 |
|------|------|------|
| `WX_DLH_CA_HOST_DIR` | `~/.config/wx-dlh/ca` | 宿主机上的 CA 目录。和原生安装同一个，已经信任过的 CA 直接接着用 |
| `WX_DLH_BIND` | `127.0.0.1` | 端口发布在宿主机哪个地址上。`0.0.0.0` 会把一个不带认证的代理和面板暴露给整个局域网 |
| `WX_DLH_PROXY_PORT` | `8899` | 代理端口，宿主机和容器里用同一个号，面板上显示的地址因此就是 WeChat 该填的地址 |
| `WX_DLH_SERVER_PORT` | `8080` | 面板端口，同上 |
| `WX_DLH_UID` / `WX_DLH_GID` | 挂载目录的属主 | 容器写文件用的身份，见下 |
| `WX_DLH_MEDIA_TOOLS` | `0` | `1`：构建时装上 ffmpeg 和 tesseract |

容器日志里打印的是容器内路径，对照关系：

| 容器内 | 宿主机 |
|--------|--------|
| `/data/ca` | `WX_DLH_CA_HOST_DIR`（默认 `~/.config/wx-dlh/ca`） |
| `/data/captures` | `./captures` |
| `/data/downloads` | `./downloads` |
| `/data/processed` | `./processed` |

所以 `docker compose run --rm cli ca` 打印的信任提示（`/data/ca/...`、容器里的信任库）不用照做，以上面的宿主机路径和 `scripts/trust-ca.sh` 为准。

### 文件属主

容器不以 root 写宿主机的目录。入口脚本取第一个不属于 root 的挂载目录的属主作为运行身份（通常就是你），都没有就用 `WX_DLH_UID`，再没有就是 1000；Docker 刚替你建出来、属于 root 的目录会交给这个用户。macOS / Windows 的 Docker Desktop 本来就会映射属主，这一步基本是空操作。

## 已经在用原生安装 / 从 wxclip 升级

- 已经有 `~/.config/wx-dlh/ca`：什么都不用做，容器用的就是这张已经信任过的 CA；Linux 上 flatpak override 里固定的 SPKI 也还对得上。
- 只有 `~/.config/wxclip/ca`（改名前的 CA，还没跑过新版本）：**先在宿主机上跑一次 `wx-dlh ca`**（会把它原样搬到新目录），或者手动 `mv ~/.config/wxclip/ca ~/.config/wx-dlh/ca`，再 `docker compose up`。容器只看得到挂进来的那一个目录，搬迁的逻辑在容器里跑不了；直接起容器会在新目录里生成一张全新的 CA，之前的信任和 SPKI 就都对不上了。
- 想让容器用别的 CA：`WX_DLH_CA_HOST_DIR=/path/to/ca docker compose up -d`。

## 安全

- 端口默认只发布在 `127.0.0.1`。代理和面板都没有认证，别轻易改成 `0.0.0.0`。
- CA 私钥（`mitmproxy-ca.pem`）留在宿主机的 CA 目录里，不进镜像、不进 Git。拿到它的人能对这台机器上信任它的程序做中间人。
- 镜像只在本地构建（`wx-dlh:local`），不推送到任何仓库。
- 代理和原生版本一样会把 flow 存档写进 `captures/flows-*.mitm`；常驻运行时它会一直增长，不需要就在 `compose.yaml` 的 `proxy` 命令里加 `--no-save-flows`。`compose.yaml` 故意没有设 `restart:`：一个中间人代理在开机后悄悄自己起来，不该是默认行为。

## 验证过什么

在 Linux（amd64，Docker 29.7 + Compose v5.5）上：

- `docker compose build`（旧构建器，没有 BuildKit）通过，镜像 235 MB，里面是 Python 3.12.12、mitmproxy 12.2.3。
- curl 通过容器里的代理访问真实的 `https://channels.weixin.qq.com/`：信任容器生成的 CA 后 TLS 正常，hook 脚本被注入，原始页面存进了宿主机的 `captures/pages/`。
- 模拟一次 hook 回传（带 `decode_key` 的 capture 事件），面板列出这条，点下载后容器直连下载、解密前 128 KiB，结果和明文逐字节一致，文件属主是宿主机用户。
- `capture` 服务：抓到一条就下载、解密并以 0 退出；`cli download --list` 能读到同一批记录。
- `docker compose stop` 不到 1 秒，代理正常收尾（打印统计、写完日志）。
- 入口脚本：挂载目录属于其他 uid 时以那个 uid 运行，并接管 Docker 新建的 root 目录。

没有验证的：

- **真实 WeChat 客户端经过容器代理播放**（三个系统都没有）。容器里的代理和原生代理是同一份代码，差别只在网络一层，但这一步要在真机上跑过才算数。
- macOS / Windows 上的 Docker Desktop。
- arm64（Apple Silicon、ARM Linux）：本机没有 QEMU 没法构建，但所有依赖都确认有 `manylinux aarch64` 的 cp312 wheel，构建不需要编译器。
