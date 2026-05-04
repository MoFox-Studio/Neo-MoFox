# Skill Manager

SkillManager 是 Neo-MoFox 的技能索引与按需加载插件。

它会在插件加载完成后扫描本地 skill 目录，建立技能清单，并提供 3 个工具给 LLM 按需调用：

- get_skill：读取并注入 SKILL.md
- get_reference：读取 skill 内 markdown 引用文件
- get_script：执行 skill 内 python 脚本（支持参数，返回脚本输出）

## 目录结构

```text
plugins/skill_manager/
├── __init__.py
├── config.py
├── manifest.json
├── models.py
├── plugin.py
├── tools.py
└── handlers/
    ├── __init__.py
    └── skillmanager.py
```

## 工作流程

1. 启动时触发 `SkillManagerLoadHandler`。
2. `SkillManagerPlugin.refresh_skill_catalog()` 扫描配置路径，发现所有包含 `SKILL.md` 的 skill。
3. 刷新 `skills` 索引并同步 system reminder（actor/sub_actor）。
4. LLM 在需要时按顺序调用工具：
   - 先 `get_skill(name)`
   - 再按需 `get_reference(name, location)` 或 `get_script(name, location, script_args)`

## Skill 发现规则

- 配置路径来自 `manager.paths`，默认是 `skill`。
- 若路径本身包含 `SKILL.md`，它被视为一个 skill 根目录。
- 否则会扫描该路径下一层子目录，子目录中存在 `SKILL.md` 的会被识别为 skill。
- `SKILL.md` front matter 支持解析：
  - `name`
  - `description`

## 工具说明

### 1) get_skill

读取指定 skill 的 `SKILL.md` 原文，并标记为“已注入”。

参数：

- `name: str` skill 名称

返回：

- 成功：`(True, <SKILL.md全文>)`
- 失败：`(False, <错误信息>)`

### 2) get_reference

读取 skill 根目录内的 markdown 引用文件。

参数：

- `name: str` 已注入 skill 名称
- `location: str` skill 内相对路径（必须是 `.md`）

约束：

- 必须先调用 `get_skill` 注入该 skill。
- 路径禁止越界（仅允许 skill 根目录内文件）。

返回：

- 成功：`(True, <markdown全文>)`
- 失败：`(False, <错误信息>)`

### 3) get_script

直接执行 skill 根目录内 python 脚本（等价命令行执行脚本），支持可选参数透传。

参数：

- `name: str` 已注入 skill 名称
- `location: str` skill 内相对路径（必须是 `.py`）
- `script_args: list[str] | str | None` 可选
  - 字符串示例：`"--check 60 --bonus 1"`
  - 列表示例：`["--check", "60", "--bonus", "1"]`

执行行为：

- 通过 `runpy.run_path(..., run_name="__main__")` 执行脚本。
- 执行时会临时设置 `sys.argv = [script_path, *script_args]`。
- 自动捕获脚本的 `stdout/stderr`（包含 print 与标准流日志输出）并拼接到返回内容。
- `SystemExit(0)` / `SystemExit(None)` 视为成功（例如 argparse `--help`）。

返回：

- 成功：`(True, "脚本已执行: xxx.py\n\n[stdout]/[stderr]...")`
- 失败：`(False, "执行脚本失败...\n\n[stdout]/[stderr]...")`

## 配置项

配置模型：`SkillManagerConfig`（`plugins/skill_manager/config.py`）

`[manager]`：

- `enabled: bool = true`
- `paths: list[str] = ["skill"]`
- `inject_actor_reminder: bool = true`
- `inject_sub_actor_reminder: bool = true`

示意：

```toml
[manager]
enabled = true
paths = ["skill"]
inject_actor_reminder = true
inject_sub_actor_reminder = true
```

## 常见调用建议

- 调用顺序固定为：先 `get_skill`，再 `get_reference` / `get_script`。
- `get_script` 优先使用字符串列表参数，避免 shell 分词歧义。
- 对 argparse 脚本可直接传 `script_args: "--help"` 获取帮助文本。

## 安全与边界

- 所有引用路径都会做目录边界校验，禁止越界访问。
- `get_reference` 仅允许 `.md`。
- `get_script` 仅允许 `.py`。
- 本插件不负责脚本沙箱隔离，脚本执行权限等同当前进程。

## 版本

- Plugin: `1.0.0`
- Manifest: `plugins/skill_manager/manifest.json`
