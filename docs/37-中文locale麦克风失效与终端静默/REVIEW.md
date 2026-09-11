# 提交前 review 记录

编队：重档（`code-reviewer` ×4 审角度 ①②③④ + `code-reviewer-deep` ×1 审角度 ⑤），
由 `review-orchestrator` 在独立 context 里跑。选重档的理由：本轮改动碰了线程与资源
生命周期（`threading.Timer` 起停、`DownloadManager` 的锁与后台下载线程、STT 热切换
线程、logging handler 装卸）。

## 第 1 轮

3 条 finding 达到 ≥80 置信门槛，全部已修。

### F1 · `--init --quiet` 下 quiet 不生效（置信 85，角度④）

`main()` 先用 `configure_logging("INFO")` 建 handler（`console` 默认 True），
`parse_args()` 之后才有 `args.quiet`；而 `--init` 分支打完四条 INFO 里程碑就
`return`，永远走不到后面那次读 `args.quiet` 的 `configure_logging`。于是
`daobidao --init --quiet` 照样往终端打，与 `cli.quiet_help`（"终端不打任何输出"）
直接冲突。

**修**：`parse_args()` 之后立刻 `configure_logging("INFO", stderr=args.verbose,
console=not args.quiet)`，在任何分支之前生效（该函数幂等，读完配置还会再调一次）。

**测试**：新增 `tests/test_main_cli_flags.py` 两条——默认 `--init` 打里程碑、
`--init --quiet` 终端零字节。后者在修复前实测为红（打出四条中文里程碑）。
顺带把 `__main__.main()` 这块一直为零的覆盖开了个口子。

### F2 · preload 下载与设置页手动下载可并发跑两份 `snapshot_download`（置信 85，角度⑤）

首启且配置的 variant 本地没有时，主线程在 `preload_model()` 里同步下载（分钟级）。
此时 settings server 已在服务，而 `DownloadManager._cache_root` 仍是 `None`（要等
load 成功返回才写入）→ `/api/models/status` 报"未下载" → 用户点「下载」→
`start()` 的 `already_downloaded` 守卫同样因 `cache_root is None` 不触发 → 起第二个
线程对同一批文件再下一遍，两个下载器无协调地写同一个 cache 目录。首启长时间无反馈
恰恰是诱导用户去点那个按钮的场景。

**修**：给 `DownloadManager` 加 `loading(variant)` 上下文管理器，`load()` 期间占住
跟下载同一个"全局单活跃"槽位，设置页那次点击被挡成 `busy`。槽位已被真下载占着时
不抢也不清（那份下载的 `_worker` 自己会在 finally 里还）。`preload_model` /
`_fallback_to_0_6b` / STT 热切换三处 load 都套上了。

**测试**：4 条——挡住并发 start、正常退出还槽位、抛异常也还槽位、不抢已被真下载
占着的槽位。

### F3 · preload 期间没有信号处理器（置信 85，角度⑤）

`signal.signal(SIGINT/SIGTERM)` 原来挂在 `preload_model()` **之后**。首启在 preload
里阻塞数分钟下载，这段窗口 Ctrl+C 产生的 `KeyboardInterrupt` 是 `BaseException`，
`except Exception` 接不住，直接冒出 `main()`：不打 `shutting_down`、不 `listener.stop()`、
不 `settings_server.stop()`、不收 PortAudio。被 `kill_stale_instance` 的 SIGTERM
命中同理。本轮把 preload 从"未下载就跳过"改成"直接 load"，把这个窗口从秒级拉到了
分钟级。

**修**：把 `listener` 的构造与 `shutdown()` / 信号处理器注册整体提到 preload 之前，
`listener.start()` 仍留在 preload 之后。构造 listener 不会开始监听（listen 在
`start()` 里），且两个平台后端的 `stop()` 对没 `start()` 过的实例都有 guard
（`hotkey_linux.py` 判 `self._thread`、`hotkey_macos.py` 判 `self._listener`），
提前构造是安全的。

**没有配测试**：复现它要把 `main()` 跑进阻塞的启动编排再投递信号，属于
`playbooks/python.md` 意义上"无法用测试复现"的一类；`__main__.main()` 的编排层
本来就是项目既有的测试空白（CLAUDE.md 有记）。改动本身是控制流位置调整，已在
代码注释里写明为什么必须在 preload 之前。

### 闸 A 顺带抓到的：新测试自己继承了宿主状态

修完三条后跑全量，新加的 `test_init_prints_milestones_by_default` 在全量里红、
单跑却绿。原因是 `--init` 分支在读配置之前就 return、不会调 `set_language`，
于是它打出的文案跟着**进程里上一次**设置的语言走 —— `test_i18n.py` 跑完把全局
语言留在了 fr，断言中文文案自然不成立。同一份代码换个执行顺序两个结论，正是
全局 `CLAUDE.md` 说的"测试继承了宿主的某样东西"。

**修**：fixture 里显式 `set_language("zh")`、用例结束还原，不再跟着全局状态走。

## 第 2 轮

复审又报 3 条 ≥80，其中一条是**第 1 轮修复自己引入的回归**。三条都已修。

### R2-F1 · F3 的修复引入回归：preload 期间收到信号会挂死（置信 92，角度①③⑤）

`signal_handler` 只调 `shutdown()` 然后正常 return。CPython 的信号语义是：处理器
返回后主线程**回到被打断处继续执行** —— 也就是仍在 `preload_model()` 里同步阻塞的
`snapshot_download` 会把剩下的几分钟跑完，然后一路装配下去。而此时 `shutdown()` 已经
把 listener / worker / PortAudio / settings server 全收了、`_shutting_down` 也置了 True。

- **macOS（默认开托盘）**：走到 `tray_icon.run()` 永久阻塞主线程；`shutdown()` 闭包
  当时拿不到 `tray_icon` 引用，再按一次 Ctrl+C 又被 `if _shutting_down: return` 挡掉
  → 只能 SIGKILL。
- **Linux / `--no-tray`**：`_shutdown_event` 已 set，`wait()` 立即返回，不受影响。

**这是净回归**：改之前那个窗口里 Ctrl+C 是「不清理但确实退出」，改之后变成
「打印了正在退出、端口也放了，进程却还在」—— 更隐蔽，而且老进程占着资源却探测不到，
新实例又会起一份。

**修**：两处。① `preload_model()` 返回后立刻 `if _shutting_down: _final_exit(); return`，
不再往下装配；② 把 `tray_icon` 提前声明到 `shutdown()` 定义之前，让 `shutdown()` 能
`tray_icon.stop()` —— 顺带堵上「正常运行时 Ctrl+C，macOS 托盘不退」这个同源的老问题。

### R2-F2 · `loading()` 只挡住了一个方向（置信 83，角度①⑤）

第 1 轮加的 `loading()` 在槽位已被真下载占着时 `acquired=False`，但**照常 `yield`** ——
被包裹的 `load()` 该调 `snapshot_download` 还是调。于是反方向（用户点「下载 1.7B」，
没下完就把识别模型下拉切到 1.7B → `_switch_stt_variant` 里的 load）仍会两份并发写
同一个 cache 目录，而两个 variant 的 `allow_patterns` 都含共享的 `tokenizer/*`。

**修**：加一把真锁 `_download_lock`，`loading()` 与 `_worker` 的 `snapshot_download`
都在它下面跑，双向互斥。锁序恒为 `_download_lock` → `_lock`，不反向。下载在前时
`loading()` 等它下完再进 body —— 等待是对的，文件本来就正在被拉下来。

**测试**：新增一条用真线程验顺序（`download_start` → `download_end` → `load_body`），
修复前实测为红（load body 在下载中途就跑了）。

### R2-F3 · SUMMARY 的「后续 TODO」内联复述、没引用 issue（置信 90，角度③）

项目 `CLAUDE.md` 明文要求 per-round SUMMARY 的 TODO 引用 issue 号而不是内联复述，
否则线索只活在这一轮文档里、不会进 `docs/BACKLOG.md` 的速览索引。

**修**：建了 #19 / #20 / #21，SUMMARY 改成引用，BACKLOG 相应分组各加一行。

## 收敛状态：2 轮上限，留痕放行

`/review-loop` 的自动修复上限是 2 轮，现已用满。第 2 轮的三处修复本身**没有再经过
独立 context 复审**（那将是第 3 轮）。已跑的验证：

- 闸 A 全量测试通过
- R2-F2 有新测试覆盖（修复前为红）
- R2-F1 无自动化测试 —— 复现要把 `main()` 跑进阻塞的启动编排再投递信号，属
  `__main__.main()` 那块既有的测试空白，已开 #20 追踪

**留给 `/finish` 时人工看一眼的**：R2-F1 那两处改动（preload 后的早退守卫、
`shutdown()` 里停托盘）是控制流位置调整，建议 review 时重点确认
`tray_icon` 闭包读取时机与 `_final_exit()` 的调用点。

## 置信 <80、按规则丢弃的项（存档）

- 新增注释沿用项目既有的"写死轮次编号"风格，违反 `playbooks/python.md` §3.4，
  但属对既有惯例的延续而非孤立新问题（置信 70）。
- `self.stt` 被 preload 与热切换线程无锁交替写，可能相互覆盖（置信 78）。与 F2
  同源，但热切换只能由设置页触发、而设置页在 preload 期间会被 `loading()` 挡住，
  实际窗口已随 F2 的修复收窄。
- `cache_root` property 加锁与 `variant_states()` 注释形成潜在死锁陷阱，但当前
  代码路径不死锁（置信 70）。
- 慢下载提示 Timer 有毫秒级"先 fire 后 cancel"的错序窗口，纯观感（置信 60）。

角度①（契约与装配）、②（缺陷定向扫描，含依赖 / 锁文件核查）报 clean。
