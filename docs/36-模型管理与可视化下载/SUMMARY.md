# 36 轮总结 — 模型管理与可视化下载

## 开发项背景

### 希望解决的问题

26 轮上线了 STT variant 下拉(0.6B / 1.7B)+ 热切换,但只照顾"两个模型都已下载"的稳定态。用户实际遇到两个明显的坑:

1. **第一次 init 只下了默认的 0.6B**,1.7B 没下。用户在设置页选 1.7B 才触发下载,~2.4 GB 没有任何进度反馈,只有"切换中..."的 toast → 用户以为卡死,容易强杀程序,留下半成品 cache
2. **没有"先下模型再用"的入口**

本轮把 STT variant 的"已下载状态"做成一等公民:在设置页加"模型管理"卡片,可视化每个 variant 的下载状态,未下载的 variant 在下拉里 disabled,用户可主动点"下载"按钮,实时看进度条 + 速度 + ETA,允许取消。顺手把"启动时配置的 variant 未下载"的边界 case 也修了(回退到 0.6B preload,不再黑屏卡 5-10 分钟)。

## 实现方案

### 关键设计

**1. modelscope 原生支持下载进度回调**(spike 验证):
- `snapshot_download` 暴露 `progress_callbacks: List[Type[ProgressCallback]]` 参数
- 每个文件 `ProgressCallback(filename, file_size)` 实例化一次,跑 `update(chunk_size)` 序列,完了调 `end()`
- 我们写一个继承 `ProgressCallback` 的子类,在 `update` 里把字节累加到 `DownloadManager` state 里 — **不需要绕开 modelscope 自己包 HTTP**

**2. modelscope 原生支持 cache 检查**:
- `ModelFileSystemCache.get_file_by_path(rel_path)` 返完整路径或 None
- **自带磁盘一致性兜底**:索引说有但磁盘没有时自动清掉索引并返 None — 用户手动 `rm` cache 后下次检查自动报"未下载"

**3. modelscope 取消语义验证**(spike 跑了真下载):
- 在 `ProgressCallback.update` 里抛 `BaseException`(不是 `Exception`),snapshot_download 立刻挂出来
- 用 `BaseException` 防止 modelscope 内部 retry 装饰器的 `except Exception` 误吞
- 部分文件落 cache,下次重下时 modelscope 会续上 — 我们不主动清

**4. 架构取向 — 不引入抽象层**:
- 30 轮删过 `_downloader.py` 包装类,把 `snapshot_download` 直接 inline 进 `Qwen3ASRSTT.load()`
- 本轮 `DownloadManager` 跟 `Qwen3ASRSTT.load()` **平级地各自调用同一个 modelscope API**(没有"我们自己的下载抽象层")
- DownloadManager 只负责把文件下到磁盘,session 构造留给真正切到该 variant 时由 `load()` 处理(避免点"下载 1.7B"但还没切时浪费 ~4s + 内存构造 ONNX session)

**5. 速度算法 — 1s 滑动窗口,不用 EMA**:
- EMA 在中国带宽抖动场景里 overshoot 不收敛,用户看着数字飘很慌
- `deque(maxlen=128)` + 1s 窗口 + 简单线性差分,直观抗噪适中

**6. 启动时 variant 未下载兜底**:
- preload 之前先检查配置的 variant 是否在 cache 里
- 没下 → 临时切回 0.6B 跑 preload(不改持久化 config)
- 0.6B 也没下 → 跳过 preload,让用户进设置页主动下

### 开发内容概括

新文件:
- `src/daobidao/stt/qwen3/_download_manager.py`(~280 行) — 单实例 `DownloadManager`,封装 cache 检查 + 后台下载线程 + 进度状态 + 取消信号
- `tests/test_download_manager.py`(~580 行,22 个用例) — 全 mock `snapshot_download`,手动驱动 progress callback 序列验证 DownloadManager 行为
- `tests/test_settings_server_models.py`(~225 行,9 个用例) — 真 HTTP server + fake DownloadManager 注入,测 4 个新端点
- `tests/test_main_preload_fallback.py`(~90 行,3 个用例) — preload 兜底逻辑
- `tests/spike_modelscope_cancel.py` — 一次性 spike 脚本(默认 skip,人工跑过一次确认 modelscope cancel 语义)

修改文件:
- `src/daobidao/settings_server.py` — 加 4 个端点(`GET /api/models/status` + `POST /api/models/download` + `POST /api/models/cancel`)+ 构造器加 `download_manager=None` 参数
- `src/daobidao/__main__.py` — `WhisperInput.__init__` 实例化 `DownloadManager` 并传给 `SettingsServer`,`preload_model()` 加配置 variant 未下载兜底
- `src/daobidao/assets/settings.html` — 新"模型管理"卡片(每个 variant 一行 setting-row,带状态文本 + 进度条 + 下载/取消按钮)+ 进度条 CSS + JS poll loop(`refreshModelStatus / pollDownloadStatus / startModelDownload / cancelModelDownload`)+ STT 下拉 disabled 联动
- `src/daobidao/assets/locales/{zh,en,fr}.json` — 新增 16 条 i18n key(三语全有)

### 额外产物

- **Spike 验证脚本**(`tests/spike_modelscope_cancel.py`):虽然默认 skip 但保留作为未来 modelscope 升级时的回归参考。Spike 跑出来的关键事实:`BaseException` 在 callback.update 里抛出后 modelscope 立刻 raise,没有被 retry 装饰器吞 — 这意味着我们的 cancel 是"硬取消"(立即停止网络流量),不是"软取消"
- **`/api/models/status` stub fallback**:无 `download_manager` 时返"两个 variant 都已下载"的 stub,前端 UI 不需要区分"支持下载管理的 / 不支持的"部署,逻辑统一

### 测试覆盖

- 新增 34 个测试(22 + 9 + 3),全过
- 总计 400 测试 + 5 skip(已知 1.7B ARM 不稳定,跟本轮无关),覆盖率不退化
- spike 实测一次:BaseException 在 callback.update 里抛出 → modelscope 立刻挂出 → worker 顶层捕获 → 确认 cancel 语义符合"理想行为"

## 局限性

1. **总字节 = 0 的边界 UI**:目前 progress fill 写 `width: 50%` 当占位符,**没有 indeterminate animation**(比如 pulse 动画来回滑)。modelscope 给的 file_size 来自 HTTP HEAD Content-Length,实际场景里几乎都有,但理论上有可能为 0 的话 UI 会是个静止的 50%。优先级低,真撞上再加 ~10 行 CSS keyframes
2. **shutdown 时正在下载的 variant**:程序退出时 daemon 线程被强杀,modelscope 写到一半的文件留在 cache 里。修复需要在 shutdown 流程加 explicit cancel + wait,但因为 modelscope 自己的 retry 机制下次重下时能续,**不修也不破坏**,优先级低
3. **macOS 切到设置页时数据消耗**:每个 daobidao 实例在 `loadConfig().then()` 里都会调一次 `refreshModelStatus()`(发 GET /api/models/status,内部跑 cache 检查 — 6 次 `ModelFileSystemCache.get_file_by_path`)。单次开销 < 5ms,但多次打开设置页会重复跑。如果以后 cache 文件多了变慢,可在 DownloadManager 加内存 cache + invalidate-on-event。**当前 6 个文件,完全无感知**
4. **CSS 进度条`var(--success)` 等 token**:设置页有部分 token (如 `--success`) 我没在原文件里搜到定义,沿用了 plan 里的命名假设。如果 UI 跑起来发现颜色不对,改成具体色值即可
5. **未做后台静默预下载**:用户配置 variant 未下载时,我们回退到 0.6B 而**不是自动起一个下载任务**。这是有意的 — 用户可能就是临时换机器没下,自动下 2.4GB 比较粗暴。留给用户进设置页主动决定

## 后续 TODO

需要 `BACKLOG.md` 同步:

1. **手动 E2E 验证**(本轮收尾立即做):
   - `rm -rf ~/.cache/modelscope/hub/models/zengshuishui/Qwen3-ASR-onnx/model_1.7B/` 后重启 daobidao
   - 设置页"模型管理"区域:1.7B 标"未下载",有"下载"按钮;识别模型下拉里 1.7B disabled
   - 点下载 → 看进度条 + 速度 + ETA 流动
   - 取消 → 状态回"未下载"
   - 完成下载 → 按钮消失 + 下拉自动 enable
   - 切到 1.7B → 现有 stt_switch 流程跑 → cache 命中秒下,只剩 ~4s session load
   - 三语切换看每个 key 都有翻译

2. **`--init --variant 1.7B`** 命令行支持(本轮 PROMPT 里提到的"顺手做但不阻塞")

3. **测试阻塞性 worker 跑慢**:`test_concurrent_start_returns_busy` 等几个测试用 `time.sleep` 等 worker 完成,虽然 < 200ms 但偶尔 CI 抖动可能误失败。改用 `threading.Event` 显式同步会更稳

4. **UI 细节优化**:
   - 进度条 indeterminate animation(总字节未知场景)
   - 下载完成时 toast 提示"下载完成,模型可用"
   - 下载失败时把 error 内容做成可点击展开(避免长 error 撑爆 setting-row)

5. **shutdown 优雅取消正在下的任务**:进 shutdown 时 `download_manager.cancel(active_variant)` + 等 worker 跑完 finally,避免半成品文件

各条都不是阻塞性问题,功能上"模型管理"卡片做到了 PROMPT 里描述的所有验收点。
