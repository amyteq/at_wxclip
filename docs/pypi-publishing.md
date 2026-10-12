# 把 wx-dlh 发布成 pip 包

目标：任何人 `pip install "wx-dlh[proxy]"` 就能用，和 `pip install yt-dlp` 一样。这份文档写给**发布的人**；用户怎么装见 `README.md` 的「快速开始」。

对应 issue [#11](https://github.com/amyteq/at_wxclip/issues/11) 里[这条评论](https://github.com/amyteq/at_wxclip/issues/11#issuecomment-6115642225)。

## 现状（2026-10-12）

- **包已经能发**：`uv build` 产出 wheel 和 sdist，`twine check` 两个都过；wheel 装进全新的 Python 3.11 / 3.12 环境（带 `[proxy]`）后，`wx-dlh --version` 正常，`wx-dlh proxy --dry-run` 能生成 CA、读到包里自带的注入脚本；从 sdist 装上之后，整套测试也能在解出来的 sdist 目录里跑通。
- **名字还空着**：PyPI 和 TestPyPI 上都没有 `wx-dlh`（`https://pypi.org/pypi/wx-dlh/json` 返回 404）。PyPI 把 `wx-dlh`、`wx_dlh`、`wx.dlh` 当成同一个名字。**谁先上传谁拥有它**，而且下面要配的 pending publisher **并不占名**（PyPI 文档原话：别人抢先注册了这个名字，你的 pending publisher 就作废）。决定要发就尽早发第一个版本。TestPyPI 与 PyPI 是两套独立的账号和名字。
- **还没发过**：下一节的几件事要 owner 先定。

## 发布前要拍板的事

1. **仓库是私有的，发 PyPI 就等于公开源码。** wheel 和 sdist 里就是全部 Python 源码和注入脚本，任何人都能下载；`[project.urls]` 指向的仓库对外人是 404。不想公开的话就别发 PyPI，可以改用私有 index、在 GitHub Release 上附 wheel，或者给有权限的人用 `pip install "wx-dlh[proxy] @ git+https://github.com/amyteq/at_wxclip"`。
2. **许可证。** 仓库里没有 `LICENSE`。代码公开了但没有许可证，别人在法律上既不能用也不能改。定了之后加 `LICENSE` 文件，并在 `pyproject.toml` 的 `[project]` 里写 `license = "<SPDX 表达式>"`（例如 `"MIT"`）和 `license-files = ["LICENSE"]`。
3. **作者 / 维护者**：`authors = [{ name = "…", email = "…" }]`，会显示在 PyPI 页面上。
4. **mitmproxy 要不要变成默认依赖。** 现在它是可选的（`[proxy]`），这样 CI 和测试不需要它，运行时依赖保持为零。代价是：只 `pip install wx-dlh` 的话，跑 `wx-dlh capture` 会提示再装 `wx-dlh[proxy]`。要做到「和 yt-dlp 一样一条命令装完就能用」，就得把它挪进 `dependencies`，这会推翻 `docs/development-plan.md` 里「运行时零依赖」那条取舍，需要单独决定。
5. **边界与措辞。** `AGENTS.md` 的 Boundaries 原样适用。README 就是 PyPI 页面的正文，末尾那段「说明」（只用于本人客户端里已能播放的内容……）会一起公开，确认措辞合适。另外 README 里的相对链接（`docs/…`）在 PyPI 上点不开。
6. **版本号**：第一版建议 `0.1.0`。版本号只在 `wx_dlh/__init__.py` 里改，`pyproject.toml` 从那里读。

## 已经配好的

| `pyproject.toml` 里的配置 | 作用 |
|---|---|
| `name = "wx-dlh"`，`[project.scripts] wx-dlh = …` | 装完就有 `wx-dlh` 命令；import 名是 `wx_dlh`（连字符不能出现在 Python 标识符里，同 `yt-dlp` / `yt_dlp`） |
| `dynamic = ["version"]` + `[tool.hatch.version]` | 版本号只有一处：`wx_dlh/__init__.py` 的 `__version__`，`wx-dlh --version`、面板、包元数据都读它 |
| `[tool.hatch.build.targets.wheel.force-include]` | 把 `web/inject.js` 和 `dashboard.*` 复制进 `wx_dlh/_web/`，装好的包不依赖仓库目录 |
| `[tool.hatch.build.targets.sdist] include = […]` | sdist 只装白名单里的文件。**不要删**：hatch 默认把 git 没忽略的文件全打进去，而这个工作目录里有过一个未跟踪的 `~/.claude-*` 目录（Claude Code 的本机配置），默认构建会把它一起打包、一起上传 |
| `readme`、`keywords`、`classifiers`、`[project.urls]` | PyPI 页面上的内容。`classifiers` 只写实测过的：Linux、Python 3.11 / 3.12；macOS / Windows 验证过（#11）再加 |

**pip 装上的只有 `wx_dlh` 包本身，`scripts/` 不在 wheel 里。** `trust-ca.sh`、`run-wechat-proxied.sh` 是给 checkout 用的；在「信任 CA」做成 `wx-dlh` 的子命令之前，pip 用户要从仓库拿这些脚本，README 里写明了。

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
4. 下面那份 workflow 加进 `.github/workflows/release.yml`。**这一步故意没在本 PR 里做**：上一节那几件事没定之前，仓库里不该有一条能把代码发到公网的 workflow。建议和 `LICENSE`、`authors` 放在同一个 PR 里加。
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
tar tzf dist/*.tar.gz                           # 只该有 wx_dlh/ web/ tests/ scripts/ tools/check-inject.mjs README.md pyproject.toml PKG-INFO .gitignore
unzip -l dist/*.whl                             # 必须有 wx_dlh/_web/inject.js 和 dashboard.*
uvx twine check dist/*
```

再到一个**仓库外面**的空目录里装 wheel 跑一遍（在仓库目录里跑，import 到的是源码，测不出 wheel 缺文件）：

```bash
WHL="$(ls "$PWD"/dist/*.whl)"
cd "$(mktemp -d)"
uv venv -p 3.11 && uv pip install "${WHL}[proxy]"
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
.venv/bin/pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "wx-dlh[proxy]"
.venv/bin/wx-dlh --version
```

### 3. 正式发布

```bash
git tag -a v0.1.0 -m "wx-dlh 0.1.0" && git push origin v0.1.0
gh release create v0.1.0 --generate-notes      # 触发 release.yml → PyPI（pypi 环境放行后）
```

不用 workflow 的话：`UV_PUBLISH_TOKEN=<PyPI 的 token> uv publish dist/*`。

发完在干净环境里 `pip install "wx-dlh[proxy]==0.1.0"` 装一次确认。

**PyPI 上的版本号不能复用。** 传错了不能删了重传同一个号，只能 [yank](https://docs.pypi.org/project-management/yanking/)（之后 `pip install wx-dlh` 默认不再选它，钉了版本号的人仍能装到），再发下一个版本。

## 用户怎么装（发布之后）

```bash
pipx install "wx-dlh[proxy]"        # 或 uv tool install "wx-dlh[proxy]"：独立环境，命令进 PATH
pip install "wx-dlh[proxy]"         # 装进当前环境
pip install -U "wx-dlh[proxy]"      # 升级
```

推荐 pipx / `uv tool`：它们给 wx-dlh 一个单独的环境。别把它和 `anthropic>=1` 装进同一个环境 —— 两者要的 `h11` 版本互斥（见 `docs/development-plan.md`「总结用标准库发请求」），装在一起总有一边坏。

## 参考

- Python Packaging User Guide：<https://packaging.python.org/en/latest/tutorials/packaging-projects/>
- PyPI Trusted Publishers：<https://docs.pypi.org/trusted-publishers/>，pending publisher：<https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/>
- uv 构建与发布：<https://docs.astral.sh/uv/guides/package/>
- `pypa/gh-action-pypi-publish`：<https://github.com/pypa/gh-action-pypi-publish>
