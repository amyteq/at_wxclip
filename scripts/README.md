# scripts

给人手动执行的入口脚本，一个脚本干一件事，保持可读。

- `setup.sh` —— 创建 `.venv` 并安装依赖（`make setup` 调用它）

需要被 Python 代码复用的逻辑放进 `wxclip/`，不要留在脚本里。
