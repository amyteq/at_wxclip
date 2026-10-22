# 把 wx-dlh 发布成 pip 包

目标：任何人 `pip install wx-dlh` 就能用，和 `pip install yt-dlp` 一样。这份文档写给**发布的人**；用户怎么装见 `README.md` 的「快速开始」。

对应 issue [#11](https://github.com/amyteq/at_wxclip/issues/11) 里[这条评论](https://github.com/amyteq/at_wxclip/issues/11#issuecomment-6115642225)。

## 现状（2026-10-12）

- **包已经能发**：`uv build` 产出 wheel 和 sdist，`twine check` 两个都过；wheel 装进全新的 Python 3.11 / 3.12 环境后，`wx-dlh --version` 正常，`wx-dlh proxy --dry-run` 能生成 CA、读到包里自带的注入脚本；从 sdist 装上之后，整套测试也能在解出来的 sdist 目录里跑通。
- **名字还空着**：PyPI 和 TestPyPI 上都没有 `wx-dlh`（`https://pypi.org/pypi/wx-dlh/json` 返回 404）。PyPI 把 `wx-dlh`、`wx_dlh`、`wx.dlh` 当成同一个名字。**谁先上传谁拥有它**，而且下面要配的 pending publisher **并不占名**（PyPI 文档原话：别人抢先注册了这个名字，你的 pending publisher 就作废）。TestPyPI 与 PyPI 是两套独立的账号和名字。
- **还没发过，也暂时不发**：等开源，见下一节第 1 条和「开源那天要做的」。

## 已经定了的

owner 在 [#24 的 review](https://github.com/amyteq/at_wxclip/pull/24#discussion_r4244264446) 里定的（2026-10-12）：

1. **先开源，再发 PyPI。** 测试稳定之后把仓库改成公开，同时开 donation；PyPI 在那之后才发。仓库私有时发 PyPI 等于单方面公开源码（wheel 和 sdist 里就是全部源码和注入脚本），`[project.urls]` 指向的仓库对外人也是 404。在那之前要装就用 `pip install "wx-dlh @ git+https://github.com/amyteq/at_wxclip"`（要有仓库权限）。代价是名字这段时间里可能被别人先注册，见上一节。
2. **许可证：MIT。** 全文在仓库根目录的 `LICENSE`；`pyproject.toml` 里写的是 `license = "MIT"` 和 `license-files = ["LICENSE"]`。这是 PEP 639 的写法，要 hatchling ≥ 1.27（`[build-system]` 里钉了）；PEP 639 用它取代了 `License :: …` 那种 classifier（后者已弃用），所以 `classifiers` 里不再写许可证。
3. **作者**：`authors = [{ name = "Harry Sun", email = "harrysun2006@gmail.com" }]`，名字和邮箱都会显示在 PyPI 页面上。
4. **mitmproxy 是默认依赖。** 它在 `dependencies` 里，`pip install wx-dlh` 一条命令就能 `capture`，不再有 `[proxy]` extra。这推翻了原来的「运行时零依赖」，新的取舍是「运行时依赖只有 mitmproxy 一个」，记在 `docs/development-plan.md`「技术选型」表下面。
5. **边界与措辞**：`AGENTS.md` 的 Boundaries 原样适用，README 末尾「说明」那段就按现在的写法随 PyPI 页面公开。README 里的相对链接（`docs/…`）在 PyPI 上点不开，这一点接受；写给 PyPI 读者看的新内容用纯文本或绝对链接。
6. **第一版是 `0.1.0`。** 仓库里现在还是 `0.0.1`，发版那次再改（见「每次发版」第 1 步）。版本号只在 `wx_dlh/__init__.py` 里改，`pyproject.toml` 从那里读。

## 开源那天要做的

2026-10-22 的公开准备只处理 GitHub 源码展示及外部互动/自动化限制，不同时发布 PyPI、启用 donation 或安装 release workflow。先完成 [公开检查清单](public-release.md)。下面的 PyPI 与 donation 步骤保留为独立发布计划，执行前另行确认；公开源码本身不表示这些步骤已完成。

按顺序：

1. **确认可以开源了**：`make lint test` 通过，真机上 `wx-dlh capture` 也稳定。这一条只有 owner 能判断。
2. **公开前过一遍 git 历史**：公开的是整个历史，不只是当前文件。确认没有误提交过 token、`.env`、CA 私钥、抓到的媒体或流量（`AGENTS.md` 禁止提交的那几类），例如跑一次 [gitleaks](https://github.com/gitleaks/gitleaks) 的 `gitleaks git`。
3. **改成公开**：GitHub 仓库 → Settings → General → Danger Zone → Change repository visibility。
4. **开 donation**：先选平台，再在两处挂上链接。
   - 平台还没定。GitHub Sponsors 要先用个人账号申请开通、填好收款信息；也可以用 Ko-fi、Buy Me a Coffee、Liberapay，或者任意一个收款页面的 URL。
   - `.github/FUNDING.yml`：仓库页面上就会出现 Sponsor 按钮。格式见 [GitHub 文档](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/displaying-a-sponsor-button-in-your-repository)，例如 `github: [harrysun2006]`，或 `custom: ["<收款页 URL>"]`。
   - `pyproject.toml` 的 `[project.urls]` 加一行 `Funding = "<同一个 URL>"`，PyPI 页面左栏会显示它。改了 `pyproject.toml` 就要发新版本才会生效，所以最好在发 `0.1.0` 之前加好。
5. **做下一节的「一次性准备」**，然后按「每次发版」发 `0.1.0`。

## 已经配好的

| `pyproject.toml` 里的配置 | 作用 |
|---|---|
| `name = "wx-dlh"`，`[project.scripts] wx-dlh = …` | 装完就有 `wx-dlh` 命令；import 名是 `wx_dlh`（连字符不能出现在 Python 标识符里，同 `yt-dlp` / `yt_dlp`） |
| `dynamic = ["version"]` + `[tool.hatch.version]` | 版本号只有一处：`wx_dlh/__init__.py` 的 `__version__`，`wx-dlh --version`、面板、包元数据都读它 |
| `[tool.hatch.build.targets.wheel.force-include]` | 把 `web/inject.js` 和 `dashboard.*` 复制进 `wx_dlh/_web/`，装好的包不依赖仓库目录 |
| `[tool.hatch.build.targets.sdist] include = […]` | sdist 只装白名单里的文件。**不要删**：hatch 默认把 git 没忽略的文件全打进去，而这个工作目录里有过一个未跟踪的 `~/.claude-*` 目录（Claude Code 的本机配置），默认构建会把它一起打包、一起上传 |
| `dependencies = ["mitmproxy>=11,<13"]` | 唯一的运行时依赖，装 wx-dlh 就带上它 |
| `license`、`license-files`、`authors` | MIT 许可证（`LICENSE` 进 wheel 和 sdist）和作者，见上面第 2、3 条 |
| `readme`、`keywords`、`classifiers`、`[project.urls]` | PyPI 页面上的内容。`classifiers` 只写实测过的：Linux、Python 3.11 / 3.12；macOS / Windows 验证过（#11）再加。开 donation 时在 `[project.urls]` 加 `Funding` |

**pip 装上的只有 `wx_dlh` 包本身，`scripts/` 不在 wheel 里。** 信任 CA 是 `wx-dlh ca --install`，包里就有；`trust-ca.sh` 是它的 shell 版，留给没装 wx-dlh 的机器（例如 Docker 的宿主机；那台机器要有 openssl，子命令不用）。pip 用户还要从仓库拿的只剩 Linux 上带代理启动 WeChat 那一套（`run-wechat-proxied.sh` 和它编译的 preload shim），README 里写明了。

## 一次性准备

1. 在 <https://pypi.org> 和 <https://test.pypi.org> 各注册一个账号（两边不通用），都开两步验证 —— PyPI 要求账号开了 2FA 才能上传。
2. **推荐用 Trusted Publishing**：GitHub Actions 用 OIDC 直接向 PyPI 证明「我是这个仓库的这个 workflow」，任何地方都不用存 API token。项目还不存在，所以加的是 pending publisher：PyPI 右上角账号 → Your account → Publishing → Add a new pending publisher → GitHub，填：
   - PyPI Project Name：`wx-dlh`
   - Owner：`amyteq`
   - Repository name：`at_wxclip`
   - Workflow name：`release.yml`
   - Environment name：`pypi`

   TestPyPI 上同样加一个，Environment name 填 `testpypi`。
3. GitHub 仓库 → Settings → Environments，建 `pypi` 和 `testpypi` 两个环境；`pypi` 建议加 required reviewers，正式发版要有人点一下才放行。
4. 只有单独批准 PyPI 发布、并重新核对 Actions/App 触发策略后，才把下面那份 workflow 加进 `.github/workflows/release.yml`。公开 GitHub 源码时不自动添加它；它不属于当前手动检查 workflow。
5. 不用 Trusted Publishing 的话，就在 PyPI 建 API token：第一次上传之前只能建整个账号范围的 token，传完第一个版本后换成只限 `wx-dlh` 的 project-scoped token。本地用环境变量 `UV_PUBLISH_TOKEN` 交给 `uv publish`，不要写进任何文件。

### `.github/workflows/release.yml`

```yaml
name: release

on:
  release:
    types: [published]        # 在 GitHub 上发布一个 Release → 发到 PyPI
  workflow_dispatch:          # 手动触发 → 默认发到 TestPyPI
    inputs:
      target:
        description: "testpypi or pypi"
        required: true
        default: testpypi

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install build twine
      - run: python -m build
      - run: python -m twine check dist/*
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: ${{ github.event_name == 'release' && 'pypi' || inputs.target }}
    permissions:
      id-token: write         # Trusted Publishing：不存 token
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          repository-url: ${{ (github.event_name == 'release' || inputs.target == 'pypi') && 'https://upload.pypi.org/legacy/' || 'https://test.pypi.org/legacy/' }}
```

## 每次发版

### 1. 本地构建并自检

```bash
git switch main && git pull && git status       # 工作区要干净
make lint test
# 改版本号：只改 wx_dlh/__init__.py 里的 __version__，单独提交
rm -rf dist && uv build                         # dist/wx_dlh-<版本>.tar.gz 和 dist/wx_dlh-<版本>-py3-none-any.whl
tar tzf dist/*.tar.gz                           # 只该有 wx_dlh/ web/ tests/ scripts/ tools/check-inject.mjs README.md LICENSE pyproject.toml PKG-INFO .gitignore
unzip -l dist/*.whl                             # 必须有 wx_dlh/_web/inject.js、dashboard.* 和 licenses/LICENSE
uvx twine check dist/*
```

再到一个**仓库外面**的空目录里装 wheel 跑一遍（在仓库目录里跑，import 到的是源码，测不出 wheel 缺文件）：

```bash
WHL="$(ls "$PWD"/dist/*.whl)"
cd "$(mktemp -d)"
uv venv -p 3.11 && uv pip install "$WHL"
.venv/bin/wx-dlh --version
.venv/bin/wx-dlh proxy --dry-run --ca-dir ./ca  # 能打出计划 = mitmproxy 和注入脚本都在
```

### 2. 先发 TestPyPI

用 Trusted Publishing：GitHub → Actions → release → Run workflow，`target` 填 `testpypi`。或者在本地用 token：

```bash
UV_PUBLISH_TOKEN=<TestPyPI 的 token> uv publish --publish-url https://test.pypi.org/legacy/ dist/*
```

从 TestPyPI 装回来试（依赖 mitmproxy 还是从正式 PyPI 取，TestPyPI 上没有它）：

```bash
cd "$(mktemp -d)" && python3.11 -m venv .venv
.venv/bin/pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ wx-dlh
.venv/bin/wx-dlh --version
```

### 3. 正式发布

```bash
git tag -a v0.1.0 -m "wx-dlh 0.1.0" && git push origin v0.1.0
gh release create v0.1.0 --generate-notes      # 触发 release.yml → PyPI（pypi 环境放行后）
```

不用 workflow 的话：`UV_PUBLISH_TOKEN=<PyPI 的 token> uv publish dist/*`。

发完在干净环境里 `pip install "wx-dlh==0.1.0"` 装一次确认。

**PyPI 上的版本号不能复用。** 传错了不能删了重传同一个号，只能 [yank](https://docs.pypi.org/project-management/yanking/)（之后 `pip install wx-dlh` 默认不再选它，钉了版本号的人仍能装到），再发下一个版本。

## 用户怎么装（发布之后）

```bash
pipx install wx-dlh        # 或 uv tool install wx-dlh：独立环境，命令进 PATH
pip install wx-dlh         # 装进当前环境
pip install -U wx-dlh      # 升级
```

推荐 pipx / `uv tool`：它们给 wx-dlh 一个单独的环境。别把它和 `anthropic>=1` 装进同一个环境 —— wx-dlh 依赖的 mitmproxy 和 `anthropic>=1` 要的 `h11` 版本互斥（见 `docs/development-plan.md`「总结用标准库发请求」），pip 要么解不出来，要么装完总有一边坏。

## 参考

- Python Packaging User Guide：<https://packaging.python.org/en/latest/tutorials/packaging-projects/>
- PyPI Trusted Publishers：<https://docs.pypi.org/trusted-publishers/>，pending publisher：<https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/>
- uv 构建与发布：<https://docs.astral.sh/uv/guides/package/>
- `pypa/gh-action-pypi-publish`：<https://github.com/pypa/gh-action-pypi-publish>
