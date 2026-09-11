# 开发总结

## 开发项背景

在第一台**系统界面为简体中文**的机器（Ubuntu 24.04 / GNOME / X11 / PipeWire，
`LANG=zh_CN.UTF-8`）上装 daobidao 1.0.5，撞出两个此前一直潜伏的问题；本轮修复
过程中的真机验证又暴露了两个未发版的缺陷，一并收进本轮。

1. **中文 locale 下麦克风完全探测不到（P0，用户可见）**。麦克风插好、系统识别
   正常、是默认输入源、未静音，但按住热键毫无反应，日志里每次都是
   `mic_offline / probe_failed / no available input port`。
2. **终端零输出，启动看起来卡死在模型下载（P1，用户可见）**。终端全程只有
   modelscope 打的一行 `Downloading 9 files from ...`，之后再无输出；首次安装
   真下载 415 秒，那一行一直不动。
3. **`_download_manager` 启动即崩（未发版）**。36 轮从
   `modelscope.hub.file_download` 取 `ModelFileSystemCache`，该 re-export 在
   modelscope 1.40 起没了，而依赖声明是 `>=1.35.4`、用户实际装到 1.40。
4. **「这个模型下过没有」判定恒为 False（未发版）**。同样是 36 轮的代码，自己
   计算 modelscope 的缓存落点，在 1.40 上算不准。

3 和 4 都只存在于 master，`v1.0.5` tag 里没有 `_download_manager.py`，所以已发布
版本的用户不受影响。

## 实现方案

### 1. pactl 解析锁定 C locale

`pactl` 的输出是**本地化**的：中文桌面下 `Name:` / `Ports:` / `Active Port:` 变成
`名称：` / `端口：` / `活动端口：`（全角冒号、无空格），而
`_check_pactl_input_available()` 逐行匹配的是英文字段名，一条都对不上 →
`found_input` 恒为 False → probe 判定"没有可用输入端口"。

值得记一笔的是**只有字段名被翻译**，判定真正依赖的 `availability unknown` /
`not available` 保持英文——所以解析逻辑本身是对的，坏的只是字段名这一层。

修法：给 `subprocess.run` 传 `env={**os.environ, "LC_ALL": "C", "LANGUAGE": ""}`。
`env=` 是整体替换而非叠加，必须从 `os.environ` 拷基底，否则 `XDG_RUNTIME_DIR` /
`PULSE_SERVER` 丢了 pactl 直接连不上音频服务器。

**考虑过并否决**：改用 `pactl --format=json`（键名固定英文）。实测 pactl 16.1 的
JSON writer 遇到本地化字符串直接吐 `Invalid non-ASCII character: 0xffffffe6`，
**JSON 模式同样要先锁 locale**，一步没省；且 `--format=json` 要 PulseAudio ≥ 16.0，
换过去反而多一条版本依赖。

### 2. 控制台通道

34 轮把 terminal log 整体静默（只挂 file handler，`--verbose` 才挂 stderr），
动机成立但把用户必须看到的几条启动里程碑一起静默了。**不回滚**，改成加一条
独立的控制台通道（`logger.py` 的 `_ConsoleFilter` + `_ConsoleFormatter`）：

- **WARNING 以上一律放行**；INFO 只放行启动里程碑白名单（每次启动各出现一次，
  不随使用刷屏）
- 纯文本，只打 `message` 字段，不带时间戳 / logger 名 / key=value
- call site 一处没改，文案沿用现有 i18n
- `--verbose` 行为不变（完整 `ConsoleRenderer`，此时不叠加控制台通道）；
  新增 `--quiet` 退回全静默

「WARNING 以上一律放行」这条本身就是问题 1 的防线：`mic_offline` 是 warning，
按这个规则用户第一次按热键就会在终端看到「麦克风离线」。

另外加了首次下载提示：`snapshot_download` 超过 3 秒还没返回就打一句「首次运行
需要下载模型（0.6B 约 990 MB），可能要几分钟」。**用计时器而不是查缓存目录**——
modelscope 的缓存根路径随版本漂移，自己推路径是又一个会随环境变的假设；计时器
只依赖"这次调用有没有很快返回"。之所以需要这句：modelscope 并行下载、只打一个
**按文件计数**的聚合进度条（9 个文件），756 MB 的 decoder 会让它在同一格上停数
分钟不动。

### 3 + 4. 缓存落点交给 modelscope 自己说

两个问题同源：36 轮试图**自己算** modelscope 的缓存落点。1.40 起
`snapshot_download` 委托给 `modelscope_hub`，落点由它内部
`find_reusable_legacy_repo_dir()` 在四种历史布局之间探测决定
（`models/<owner>--<name>/snapshots/<rev>/`、`hub/models/<owner>/<name>/` …）。
这套探测复刻不了：实机上旧做法与 `local_files_only=True` 两条路都指到空的旧布局
目录，而 942 MB 真文件在新布局里。

改成**唯一可信来源是 modelscope 自己给出的路径**：

- `DownloadManager.set_cache_root()` 只接受两个来源——`Qwen3ASRSTT.load()` 成功后的
  `cache_root`，以及 `_worker` 里那次 `snapshot_download` 的返回值。
- 两个 variant 是同一 repo 下的兄弟目录，知道 root 之后 `is_variant_downloaded()`
  就只是 `Path.exists()`，零布局假设。
- `preload_model()` 不再做任何前置缓存判断，**直接 `load()`**——它本身就是权威：
  命中就秒回，没下就下载（慢了有上面那句提示）。只在 load 失败且配置的不是 0.6B
  时才回退 0.6B 重试一次。
- 依赖声明加上 `modelscope<2` 的上限。上限只挡大版本重写，1.35→1.40 这次证明小
  版本一样会破——**真正的防线是测试**（见下）。

**行为变化（有意为之）**：配置 1.7B 而本地只有 0.6B 时，不再"静默改用 0.6B"，而是
按用户配置去下 1.7B（带进度提示）。原来那个"避免启动卡在下载"的体贴行为依赖一个
算不准的前置判断，用错误的判断换体贴不划算。

### 测试

新增 / 改写的用例都把**环境显式建模成被测行为的输入**，不跟着跑测机器走：

- `test_recorder_probe.py`：桩里放一个**会看 env 的假 pactl**，没锁 C locale 就返回
  真机抓来的中文输出；再断言传给 `subprocess.run` 的 env 确实锁了 C 且没把原有变量
  掐掉。改动前必红。
- `test_logger.py`：控制台通道 7 条（白名单放行 / 白名单外不放行 / WARNING 一律放行 /
  没带 message 时的退化文案 / `--quiet` 静默但文件照写 / `--verbose` 不重复打）。
- `test_qwen3_download_hint.py`：慢下载出提示、快下载不出（验计时器真被 cancel）、
  该事件在控制台白名单里。
- `test_download_manager.py`：cache_root 未知→False、tmp 目录铺真文件→True、
  外部 rm 一个文件→False、`set_cache_root(None)` 不冲掉已知值、worker 记下
  snapshot_download 的返回值。
- `test_main_preload_fallback.py`：整份按新语义重写（不再问缓存、记 cache_root、
  load 失败回退、都失败返 False、配置本来就是 0.6B 时不重试）。

原有的 `test_download_manager.py` 之所以全绿却漏掉问题 3：所有用例都 patch 掉了
`_cache_lookup`，那段真 import **一次都没跑过**。

### 真机验证（mypc，`LANG=zh_CN.UTF-8`，modelscope 1.40.0）

```
_check_pactl_input_available() -> True      probe() -> OK

$ daobidao            # 默认
叨逼叨 - 语音输入
预加载 STT 模型...
… modelscope_hub.download | Downloading 9 files …  (1s 缓存命中)
就绪！按住热键开始说话
正在监听热键: KEY_RIGHTCTRL

$ curl /api/models/status
{"0.6B": {"downloaded": true, …}, "1.7B": {"downloaded": false, …}}

$ daobidao --quiet    # stdout / stderr 各 0 字节
```

缓存前后都是 942 M、没有多余目录，即 preload 是真命中而非重下。

全量 430 条通过（本轮前 418 条），整体行覆盖率 61% → 70%。

## 局限性

- **控制台通道会把内部 WARNING/ERROR 原样透出，其中一部分没走 i18n**。36 轮那几条
  （`Configured variant … not in cache` 之类）本轮已随 preload 重写删掉，但同类的
  英文硬编码 message 在别处可能还有，遇到再补。
- **`--no-preload` 时设置页的「已下载」会显示为未下载**。cache_root 要等一次成功的
  load 才知道，不 preload 就一直不知道；此时点「下载」会命中缓存秒回、状态转正，
  后果很轻。默认路径（有 preload）在启动 2 秒内就正确。
- **dev 环境与用户环境的 modelscope 版本仍不一致**：`uv.lock` 钉 1.35.4，用户
  `uv tool install` 解析到 1.40.0。这正是问题 3 / 4 能一路绿着漏出去的结构性成因，
  本轮没动（升 lock 会触发本地 + CI 重下 3.4 GB 模型，且可能牵出更多不兼容），
  见后续 TODO。
- **`main()` 的 CLI 编排仍无集成测试**，`--quiet` / `--verbose` 的接线只在真机手验过。
  这是既有空白，本轮没扩大也没缩小。

## 后续 TODO

- #19 — 把 `uv.lock` 的 modelscope 升到用户实际会装到的版本
- #20 — 给 `main()` 的启动编排补集成测试（CLI 开关 + 信号退出路径）
- #21 — 设置页「已下载」在 cache_root 未知时应显示第三态「未检测」
- #22 — 适配 ruff 新默认规则（36 条）+ 升 ruff 锁定版

（#22 是发版时才暴露的：CI 的 `uvx ruff` 取最新版、`uv.lock` 钉 0.15.10，ruff 0.16
把一批规则提升进默认集，而项目用的是 `extend-select`，于是没人改代码 CI 就红了 ——
本轮之前的 master 同样红 35 条。发版前先把 CI 改成 `uv run ruff check .` 跟 lock
对齐，适配新规则留给 #22。跟 #19 是同一个病。）

## 可沉淀项

三条，都不是本项目特有的：

1. **解析给机器读的输出，必须锁定 locale**（已提 [claude-code-global#164](https://github.com/pkulijing/claude-code-global/issues/164)）。这轮的 P0 就是
   pactl 输出被翻译导致解析全不匹配。同一根因适用于任何 shell-out 后解析文本的场景
   （`systemctl` / `ip` / `docker` / `git` 的部分子命令都本地化）。落点：
   `playbooks/shell.md` 已经讲「中文 × shell 语法」的坑，这条是同族的第三种形态 ——
   不是脚本里写了中文，而是**被调命令吐了中文**。判据一句话：把外部命令的输出喂给
   解析器之前，先问它会不会跟着 `LANG` 变。

2. **dev 用 lockfile 钉版本、用户走版本范围解析 → 两个环境跑的不是同一版**
   （已提 [claude-code-global#165](https://github.com/pkulijing/claude-code-global/issues/165)）。这轮问题 3 / 4 全绿漏出去的结构性成因：`uv.lock` 钉
   1.35.4，用户 `uv tool install` 拿 1.40.0，于是 CI 从来没执行过用户实际走的代码
   路径。任何有 lockfile 的栈（uv / poetry / npm / cargo）+ 库形态分发都成立。
   落点：`playbooks/python.md` 的依赖管理段。

3. **真机诊断改动了别人机器的状态，必须还原并当面告知**（本轮未提 issue，人类判断
   优先级不足；记在这里备查）。这轮为了对比 modelscope 版本行为，在用户机器上装了旧版，让 modelscope
   造出一个空的旧布局缓存目录、后来又往里重下了 44 MB；虽然全部清理并复验了，但当时
   若没主动交代，用户后来看到 `~/.cache` 多出目录只会更困惑。判据：**诊断动作只要
   在对方机器上留下了 git 之外的痕迹，就要么还原、要么说清楚。**
