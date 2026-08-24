# Skill Manager

SkillManager 是 Neo-MoFox 的技能索引与按需加载插件。

它会在插件加载完成后扫描本地 skill 目录，建立技能清单，并提供工具给 LLM 按需调用：

- get_skill：读取并注入 SKILL.md
- get_reference：读取 skill 内 markdown 引用文件
- get_script：在子进程中执行 skill 内脚本（支持 .py/.ps1/.bat/.cmd/.sh，支持参数，返回脚本输出）
  —— **默认关闭**，需在配置中显式开启

## 目录结构

```text
plugins/skill_manager/
├── __init__.py
├── config.py
├── manifest.json
├── models.py
├── plugin.py
├── README.md
├── tools.py
├── commands/
│   ├── __init__.py
│   └── skill_command.py
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

在独立子进程中执行 skill 根目录内脚本，支持可选参数透传。

> **该工具默认不注册，且开启后仍受权限门约束。** 需要在配置中把 `[security].allow_script_execution` 设为 `true` 才会出现在 LLM 的工具列表里；调用者还必须达到 `[security].script_execution_permission_level`（默认 `owner`）。详见「安全与边界」。

参数：

- `name: str` 已注入 skill 名称
- `location: str` skill 内相对路径（必须是 `.py`、`.ps1`、`.bat`、`.cmd`、`.sh` 之一）
- `script_args: list[str] | str | None` 可选
  - 字符串示例：`"--check 60 --bonus 1"`
  - 列表示例：`["--check", "60", "--bonus", "1"]`

执行行为：

- 所有脚本一律以**独立子进程**执行，统一受 15 秒超时保护。
- `.py` 通过当前解释器（`sys.executable`）执行。
- `.ps1` 通过 `pwsh` 或 `powershell` 执行，附带 `-NoProfile -NonInteractive`。
- `.bat/.cmd` 通过 `%COMSPEC%`（即 `cmd.exe`）`/c` 执行。
- `.sh` 通过 `bash` 或 `sh` 执行。
- 执行时会透传 `script_args`，并自动将工作目录设置为脚本所在目录。
- 自动捕获脚本的 `stdout/stderr`（包含 print 与标准流日志输出）并拼接到返回内容；
  子进程内的 Python 被强制以 UTF-8 输出，避免 Windows 本地代码页导致中文乱码。
- 退出码 `0` 视为成功（例如 argparse `--help`），非零视为失败并回显输出。

返回：

- 成功：`(True, "脚本已执行: xxx.py\n\n[stdout]/[stderr]...")`
- 失败：`(False, "脚本执行退出码: 1\n\n[stdout]/[stderr]...")`

## 配置项

配置模型：`SkillManagerConfig`（`plugins/skill_manager/config.py`）

`[manager]`：

- `enabled: bool = true`
- `paths: list[str] = ["skill"]`
- `inject_actor_reminder: bool = true`
- `inject_sub_actor_reminder: bool = true`

`[security]`：

- `allow_script_execution: bool = false` —— 是否允许通过 `get_script` 执行脚本。
  关闭时 `get_script` 组件根本不会注册。
- `script_execution_permission_level: str = "owner"` —— 执行脚本所需的**调用者**最低权限
  级别，可选 `guest` / `user` / `operator` / `owner`。与 `/skill` 命令用同一套权限体系。
- `powershell_bypass_execution_policy: bool = false` —— 执行 `.ps1` 时是否附加
  `-ExecutionPolicy Bypass`。关闭时沿用机器/用户的 PowerShell 执行策略。

示意：

```toml
[manager]
enabled = true
paths = ["skill"]
inject_actor_reminder = true
inject_sub_actor_reminder = true

[security]
allow_script_execution = false
script_execution_permission_level = "owner"
powershell_bypass_execution_policy = false
```

### manifest `include` 与实际注册集合

`manifest.json` 的 `include` 列出的是本插件**声明的全部组件面**（5 个），而实际注册集合由配置
决定：`manager.enabled = false` 时一个都不注册，`security.allow_script_execution = false` 时不
注册 `get_script`。`include` 只被 `loader.py` 解析成组件级依赖声明，不参与强制校验，因此这种
「清单是超集」的关系是有意为之，不是漏维护。

## 常见调用建议

- 调用顺序固定为：先 `get_skill`，再 `get_reference` / `get_script`。
- `get_script` 优先使用字符串列表参数，避免 shell 分词歧义。
- 对 argparse 脚本可直接传 `script_args: "--help"` 获取帮助文本。

## 安全与边界

`get_script` 以 bot 进程权限执行代码，而工具调用层**没有权限模型**（`tool_manager/` 下不存在
任何鉴权代码）：任何聊天用户都可以通过提示注入影响 LLM 选择的工具参数。因此这里的默认姿态是
「默认关闭、显式开启、开启后仍要看调用者是谁」。

- **脚本执行总开关默认关闭。** `[security].allow_script_execution = false` 时插件不注册
  `get_script`，LLM 的工具列表里看不到它。
- **开启后仍有调用者权限门。** 每次调用都会用触发消息的 `platform` + `sender_id` 解析
  `person_id`，查出权限级别并与 `script_execution_permission_level`（默认 `owner`）比对。
  这一层是为了避免「开关一开就对所有聊天用户放开」，让它与 `/skill` 命令的 `OWNER` 门对齐。
  以下情况一律拒绝（fail-closed）：无触发消息、身份字段为空、级别配置写错、权限查询抛错。
- **所有脚本都在子进程中运行。** `.py` 不再用 `runpy` 在 bot 进程内执行，因此脚本拿不到
  bot 进程内的对象：已加载的配置实例、内存中的数据库会话、以及 monkeypatch 全局状态
  （含权限判定函数）的能力都消失了。代价是脚本**不能再 `import src.*`**：需要框架能力的
  逻辑应写成插件组件，而不是 skill 脚本。

  这是**进程内存隔离，不是沙箱**：子进程仍以与 bot 相同的 OS 身份运行，拥有同样的文件系统
  与网络权限，可以直接读 `config/`（含 `config/model.toml` 里的模型配置）、`data/`、以及
  继承到的环境变量。想真正约束这些，需要 OS 层手段（独立低权限账号、容器、AppArmor 等），
  本插件不提供。
- **`.bat/.cmd` 参数走字符白名单。** Windows 上 `subprocess.list2cmdline` 不转义 cmd.exe
  元字符，参数里的 `&` 会逃逸成独立命令。因此这类脚本的参数只接受
  `字母、数字与 . _ : / \ @ = -`，出现空白或 `& | < > ^ ( ) " % !` 一律拒绝执行。
  需要传含空格的值时请拆成多个列表元素，或改用 `.ps1` / `.py`。
- **所有类型的参数都不接受控制字符**（含换行、回车、NUL）。
- **不再默认绕过 PowerShell 执行策略。** 执行策略是 Windows 上阻止未签名脚本运行的纵深
  防御，默认不再附加 `-ExecutionPolicy Bypass`；在受限策略的机器上运行未签名 skill 脚本
  需要运维显式打开 `[security].powershell_bypass_execution_policy`。
- **所有引用路径都会做目录边界校验**，禁止越界访问；`get_reference` 仅允许 `.md`，
  `get_script` 仅允许 `.py/.ps1/.bat/.cmd/.sh`。
- **脚本执行有 15 秒超时**，超时后进程会被 kill，并尽量回收已产生的输出。

### 已知的、有意接受的残留风险

- **调用者可以触达脚本声明的完整参数面。** `.ps1` 走 `-File`，参数作为字面字符串交给脚本的
  `param()` 绑定器（不会被重新解析成 PowerShell 代码），但以 `-` 开头的参数确实会绑定到
  命名参数上。`.py` 的 argparse、`.sh` 的 getopts 同理。这是「给脚本传参」这个功能的固有
  语义，砍掉才能消除；实际约束靠的是上面的调用者权限门与下面的可信前提。
- **脚本文件本身属于可信输入。** 本插件没有任何写盘、下载、解压逻辑，skill 目录内容完全由
  运维决定，脚本路径也因此不做元字符校验。请把「往 skill 目录放一个脚本」当作
  「授予该脚本 bot 进程权限」来对待。

## 版本

- Plugin: `1.1.0`
- Manifest: `plugins/skill_manager/manifest.json`
