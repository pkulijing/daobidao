# 36 轮实现计划 — 模型管理与可视化下载

## Context

26 轮上线了 STT variant 下拉(0.6B / 1.7B)+ 热切换,但只照顾"两个模型都已下载"的稳定态。当前痛点:第一次 init 只下了默认的 0.6B,1.7B 没下;用户在设置页选 1.7B 才触发下载,~2.4 GB 没有任何进度反馈,容易误以为程序卡死把它强杀,留下半成品 cache。

本轮目标:设置页新增"模型管理"区域,围绕"模型下载状态"做透明化:可视化每个 variant 的下载状态、未下载的 variant 在下拉里 disabled、模型管理区域里可主动下载并看到实时进度,可取消。

详细需求见 `PROMPT.md`。已确认的技术前提:modelscope 原生暴露 `progress_callbacks`(无需自己包 HTTP)、`ModelFileSystemCache.get_file_by_path` 自带磁盘一致性兜底(用户手动 rm cache 后自动报"未下载")。

---

## 模块结构

### 新增文件

| 路径 | 大小 | 职责 |
|------|------|------|
| `src/daobidao/stt/qwen3/_download_manager.py` | ~250 行 | 单实例 `DownloadManager`,封装 cache 检查 + 后台下载线程 + 进度状态 + 取消信号 |
| `tests/test_download_manager.py` | ~200 行 | DownloadManager 单元测试,monkeypatch `snapshot_download` 模拟 progress callback 序列 |
| `tests/test_settings_server_models.py` | ~150 行 | 4 个新端点的 HTTP 测试 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/daobidao/settings_server.py` | 加 4 个端点 handler + 构造器加 `download_manager=None` 参数 |
| `src/daobidao/__main__.py` | `WhisperInput.__init__` 实例化 `DownloadManager` 并传给 `SettingsServer` |
| `src/daobidao/assets/settings.html` | 新"模型管理"卡片 + 进度条 CSS(<50 行) + JS poll loop + 下拉 disabled 联动 |
| `src/daobidao/assets/locales/{zh,en,fr}.json` | ~13 条新 i18n key |

---

## 核心设计

### `DownloadManager` 接口

```python
# stt/qwen3/_download_manager.py

REPO_ID = "zengshuishui/Qwen3-ASR-onnx"

REQUIRED_FILES = {
    "0.6B": [
        "model_0.6B/conv_frontend.onnx",
        "model_0.6B/encoder.int8.onnx",
        "model_0.6B/decoder.int8.onnx",
        # tokenizer/* 几个核心文件,实施时按 qwen3_asr.py 的 allow_patterns 平移
    ],
    "1.7B": [
        "model_1.7B/conv_frontend.onnx",
        "model_1.7B/encoder.int8.onnx",
        "model_1.7B/decoder.int8.onnx",
    ],
}

class DownloadManager:
    def is_variant_downloaded(self, variant: str) -> bool:
        """走 ModelFileSystemCache.get_file_by_path,自带磁盘一致性兜底。"""

    def variant_states(self) -> dict[str, dict]:
        """返回浅拷贝,直接给 _send_json 用。每 variant 字段:
        downloaded, downloading, received_bytes, total_bytes,
        speed_bps, eta_seconds, error, cancelled
        """

    def start(self, variant: str) -> tuple[bool, str | None]:
        """触发后台下载。返回 (accepted, reason)。
        reason 为 i18n key:already_downloaded / busy / invalid_variant。
        """

    def cancel(self, variant: str) -> bool:
        """no-op 当无活跃下载或 variant 不匹配。"""
```

**并发模型**:
- `_lock: threading.Lock` 守护所有 mutable state
- `_active_variant: str | None` 实现"全局单 active 下载",`start()` 第一步检查后置入,worker `finally` 块清空
- worker 是 daemon thread,程序退出自动死
- `_cancel_event: threading.Event` 取消信号,callback.update 里检查 → `raise _DownloadCancelled` (BaseException 子类,防 modelscope 内部 except Exception 误吞)
- DownloadManager 内部 lock **绝不嵌套**外部调用(snapshot_download 在锁外跑,callback 拿锁纯增量写 state)

### ProgressCallback 工厂

modelscope 对每个文件 instantiate `(filename, file_size)`,我们传的是 **class 不是 instance**,所以用闭包工厂:

```python
def _make_callback_class(mgr: "DownloadManager", variant: str) -> type:
    class _Tracker(ProgressCallback):
        def __init__(self, filename: str, file_size: int):
            super().__init__(filename, file_size)
            mgr._on_file_start(variant, filename, file_size)
        def update(self, size: int):
            mgr._on_bytes(variant, size)  # size 是 increment
            if mgr._cancel_event.is_set():
                raise _DownloadCancelled()
        def end(self):
            mgr._on_file_end(variant)
    return _Tracker
```

**关键事实**:`update(size)` 的 size 是**增量**(modelscope `file_download.py` 第 435 行 `callback.update(len(chunk))`),不是绝对值。`_on_bytes` 必须按增量累加。

### 速度计算:1s 滑动窗口

```python
self._byte_log: deque[tuple[float, int]] = deque(maxlen=64)

def _on_bytes(self, variant: str, n: int) -> None:
    with self._lock:
        s = self._state[variant]
        s["received_bytes"] += n
        now = time.monotonic()
        self._byte_log.append((now, s["received_bytes"]))
        while self._byte_log and now - self._byte_log[0][0] > 1.0:
            self._byte_log.popleft()
        if len(self._byte_log) >= 2:
            dt = self._byte_log[-1][0] - self._byte_log[0][0]
            db = self._byte_log[-1][1] - self._byte_log[0][1]
            s["speed_bps"] = db / dt if dt > 0 else 0
```

不用 EMA(中国带宽抖动场景里 EMA overshoot 不收敛,数字飘得慌)。`deque(maxlen=64)` 兜上限。ETA = `(total - received) / max(speed, 1)`,UI 端格式化。

### 4 个新 HTTP 端点

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/models/status` | — | `{"variants": {"0.6B": {downloaded, downloading, received_bytes, total_bytes, speed_bps, eta_seconds, error, cancelled}, "1.7B": {...}}}` |
| POST | `/api/models/download` | `{"variant": "1.7B"}` | `{"ok": True/False, "reason": "..."}` |
| POST | `/api/models/cancel` | `{"variant": "1.7B"}` | `{"ok": True/False}` |

`SettingsServer` 构造器加 `download_manager=None` 参数(类比已有 `stt_switch_status_getter`)。`download_manager is None` 时端点返"两个 variant 都 downloaded=True"的 stub(测试不传时不影响 UI)。

---

## 前端 UI

### 卡片布局:独立"模型管理"卡片

放在"识别模型"卡片**下方**,DOM 树:

```
.card#model_manager_card
  .card-title  → "模型管理"
  .setting-row[data-variant="0.6B"]
    .setting-label  → "0.6B (~990 MB)"
    .setting-desc.model-status  → "已下载" / "未下载" / "下载中"
    .progress-wrapper [hidden 当 not downloading]
      .progress-bar > .progress-fill (width: x%)
      .progress-meta  → "120 / 990 MB · 8.4 MB/s · 剩 1m 42s"
    .model-actions
      button.download-btn  [hidden 当 downloaded 或 downloading]
      button.cancel-btn  [hidden 当 not downloading]
  .setting-row[data-variant="1.7B"]
    ...同上...
```

为什么不合进"识别模型"卡片:卡片合并会破坏现有"setting-row 单 setting"的视觉节奏;状态展示+异步操作 DOM 复杂得多;拆开后"识别模型下拉里某个 option disabled"是被动后果(由模型管理状态推导),用户视角清晰("想用 1.7B?去模型管理点下载")。

### 进度条 CSS(<50 行)

复用现有 CSS 变量(`--primary` / `--border` / `--success` / `--text-secondary`),关键:

```css
.progress-bar { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.progress-fill { height: 100%; background: var(--primary); width: 0%; transition: width 0.25s ease; }
.progress-meta { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.model-actions { display: flex; gap: 8px; }
.model-status.status-error { color: #d32f2f; }
.model-status.status-downloaded { color: var(--success); }
```

### 轮询逻辑

新增独立的 `pollDownloadStatus()`,跟现有 `pollSttSwitchStatus()` 解耦:
- `loadConfig()` 之后立刻调一次 `refreshModelStatus()` 初始化卡片 + 设下拉 disabled
- 任何"开始下载/取消/切换 variant 完成"事件后再调一次 `refreshModelStatus()`
- 下载进行中:`setTimeout(pollDownloadStatus, 500)` 直到 `downloading: false`
- 下载完成:停 polling 前再调一次 `refreshModelStatus()`,保证下拉 enable 状态及时更新

### STT 下拉 disabled 联动

`refreshModelStatus()` 里读 `variants[v].downloaded`,对每个 `<option>` 设 `disabled = !downloaded`,加 `title` 属性显示 i18n hint("该模型未下载,请先在模型管理中下载")。

### i18n 新增 key(13 条)

| key | zh | en | fr |
|-----|-----|-----|-----|
| `settings.model_manager` | 模型管理 | Model Management | Gestion des modèles |
| `settings.model_status_downloaded` | 已下载 | Downloaded | Téléchargé |
| `settings.model_status_not_downloaded` | 未下载 | Not downloaded | Non téléchargé |
| `settings.model_status_downloading` | 下载中 | Downloading | Téléchargement |
| `settings.model_download_btn` | 下载 | Download | Télécharger |
| `settings.model_cancel_btn` | 取消 | Cancel | Annuler |
| `settings.model_size_0_6b` | 约 990 MB | ~990 MB | ~990 Mo |
| `settings.model_size_1_7b` | 约 2.4 GB | ~2.4 GB | ~2,4 Go |
| `settings.model_progress_fmt` | {received} / {total} · {speed}/s · 剩 {eta} | {received} / {total} · {speed}/s · {eta} left | {received} / {total} · {speed}/s · {eta} restant |
| `settings.model_download_failed` | 下载失败 | Download failed | Échec du téléchargement |
| `settings.model_download_busy` | 已有下载在进行 | A download is already in progress | Un téléchargement est déjà en cours |
| `settings.model_download_cancelled` | 已取消 | Cancelled | Annulé |
| `settings.stt_variant_disabled_hint` | 该模型未下载,请先在模型管理中下载 | Not downloaded; download first in Model Management | Non téléchargé. Téléchargez-le d'abord dans Gestion des modèles |

---

## 测试策略

### `tests/test_download_manager.py` 关键 case

mock `modelscope.snapshot_download` 让它**手动驱动 progress_callbacks 序列**(实例化每个 callback class → 跑一系列 update(chunk) → end),这样 DownloadManager 行为可重复验证:

1. **`test_initial_state_all_idle`** — 实例化后 `variant_states()` 返合理 dict,所有 downloading=False
2. **`test_is_variant_downloaded_uses_modelscope_cache`** — patch `ModelFileSystemCache.get_file_by_path` 返 None / path,验证 boolean
3. **`test_is_variant_downloaded_handles_post_rm`** — get_file_by_path 第一次返 path、第二次返 None(模拟 rm),立刻拿 False
4. **`test_start_when_already_downloaded_is_noop`** — `(False, "already_downloaded")`
5. **`test_start_runs_snapshot_download_in_thread`** — `_active_variant` 在跑时设、跑完清
6. **`test_progress_callback_accumulates_bytes`** — fake snapshot_download 驱动 callback,断言 `received_bytes` 累加正确
7. **`test_progress_callback_total_bytes_sum`** — 多文件,total = Σ file_size
8. **`test_speed_window_excludes_old_samples`** — `monkeypatch.setattr(time, 'monotonic', ...)` 模拟时间推进
9. **`test_concurrent_start_rejected`** — `threading.Event` 卡住第一个 worker,第二次 `start()` 返 `(False, "busy")`
10. **`test_cancel_sets_event_and_callback_raises`** — start → cancel → 验证 `_cancel_event.is_set()` + 模拟 callback.update 抛 `_DownloadCancelled`
11. **`test_cancel_when_idle_is_noop`** — 没活跃下载时 cancel 返 False
12. **`test_state_includes_eta`** — 给定 received/total/speed,断言 eta 计算正确

### `tests/test_settings_server_models.py` 关键 case

复用 `test_settings_server.py` 已建立的 `_free_port()` + `_request()` + tmp_path config 模式:

1. **`test_get_models_status_default_when_no_manager`** — 不传 download_manager,GET 返两个 variant 都 downloaded=True 的 stub
2. **`test_get_models_status_forwards_manager_state`** — 传 fake manager(`MagicMock(variant_states=lambda: {...})`)
3. **`test_post_models_download_invokes_start`** — fake manager,POST → 验证 `manager.start("1.7B")` 被调
4. **`test_post_models_download_busy_returns_reason`**
5. **`test_post_models_download_invalid_variant_400`**
6. **`test_post_models_cancel_invokes_cancel`**

`tests/conftest.py` 不动(已有 `stt_0_6b/stt_1_7b` 等 fixture 不需要变,新测试用 fake manager,不真下)。CI `~/.cache/modelscope` 缓存预热不变。

---

## TDD 顺序

按"红 → 绿"逐步推进。每一步**先写失败测试,再写最小实现让它过**。

1. **DownloadManager 骨架**:`test_initial_state_all_idle` 红 → 类骨架 + `_state` 初始化 + `variant_states()` 浅拷贝
2. **cache 检查**:`test_is_variant_downloaded_uses_modelscope_cache` 红 → `is_variant_downloaded` 实现 + `REQUIRED_FILES` 常量
3. **start 接口骨架**:`test_start_when_already_downloaded_is_noop`、`test_start_when_idle_sets_active_variant` 红 → `start()` 加锁、检查 cache、置 `_active_variant`、起线程,worker 函数体先 `pass`
4. **progress callback 累加(核心)**:`test_progress_callback_accumulates_bytes` 红 → `_make_callback_class` 工厂 + `_on_file_start/_on_bytes/_on_file_end`
5. **speed 窗口**:`test_speed_window_excludes_old_samples` 红 → deque + 1s 公式
6. **concurrent reject**:`test_concurrent_start_rejected` 红 → `start()` 第一步 `_active_variant` 检查
7. **cancel**:`test_cancel_sets_event_and_callback_raises` 红 → `_cancel_event` + worker try/except `_DownloadCancelled`。**同时跑 spike 验证真 modelscope 行为**(见下)
8. **HTTP 端点**:三条断言 forwards 红 → 三个 handler + 构造器加参数
9. **__main__ 接线**:`test_main.py` 加一行 `assert wi._download_manager is not None`(集成)
10. **启动时 variant 未下载兜底**:`test_main_fallback_when_configured_variant_missing` 红 → preload 前的 cache 检查 + 回退 0.6B
11. **前端 UI**:手动测试为主(项目惯例不写 selenium)

---

## modelscope cancel 语义 spike

**风险点**:callback.update 抛异常时 modelscope 的精确行为未验证。三种可能:
- 理想:异常向上冒,worker 干净取消
- 温和:retry 装饰器吞了,但停止下后续文件,最终抛 `MaxRetriesExceeded`
- 糟糕:retry 吞了,继续下下一个文件

**第一天就跑 spike**(`tests/spike_modelscope_cancel.py`,`pytest.mark.skip` 默认跳过,人工跑一次):

```python
def test_cancel_via_callback_exception(tmp_path):
    from modelscope import snapshot_download
    raised = {"count": 0}
    class Cancel(BaseException): pass
    class CB:
        def __init__(self, name, size): pass
        def update(self, n):
            raised["count"] += 1
            if raised["count"] > 5:
                raise Cancel()
        def end(self): pass
    with pytest.raises((Cancel, Exception)):
        snapshot_download("zengshuishui/Qwen3-ASR-onnx",
                          allow_patterns=["model_1.7B/conv_frontend.onnx"],
                          cache_dir=str(tmp_path),
                          progress_callbacks=[CB])
```

**Fallback(行为 3)**:接受"软取消" — UI 显示已取消、`is_variant_downloaded` 因为没下完仍返 False,后台线程默默跑完。最坏情况是用户取消后又点下载,cache 命中秒过。损失:多花一次带宽。功能不破。

---

## 风险与边界

| 项 | 处理 |
|----|------|
| disk 满 | modelscope 抛 OSError,worker 捕获写 `state["error"]`,UI 显示"下载失败:磁盘空间不足"(用 i18n key) |
| 网络断 | modelscope 内部 retry,最终 `RequestException`,同上 |
| 总字节 = 0 的 callback | UI fallback:`.progress-fill` 加 indeterminate animation(`width:50%; animation: pulse...`) |
| 手动 rm 期间正在下 | 状态 `downloading=True` 仍亮,UI 用 `downloading || downloaded` 双判断 disabled |
| shutdown 时正在下 | 第一版不做 explicit cancel(daemon 线程进程退出自动死) |
| 死锁 | DownloadManager 内部一个锁,绝不嵌套外部调用;handler 拿锁纯读后立刻释放 |

---

## 验证(end-to-end)

实施完成后手动跑这套:

1. **干净状态测试**:`rm -rf ~/.cache/modelscope/hub/models/zengshuishui/Qwen3-ASR-onnx/model_1.7B/`(保留 0.6B 和 tokenizer 让程序仍能跑) → 重启 daobidao → 进设置页:
   - 模型管理那里 1.7B 标"未下载",有"下载"按钮
   - "识别模型"下拉里 1.7B 是 disabled,hover 有 hint
2. **下载流程**:点"下载 1.7B" → 看进度条流动 + 速度刷新(理想 1-3 MB/s) + 有"取消"按钮
3. **取消**:下到一半点取消 → 状态回"未下载" + 下载按钮回来 + 下拉里 1.7B 仍 disabled
4. **完成下载**:再点下载 → 等到完成 → 按钮消失 + 下拉里 1.7B 自动 enable
5. **切换**:在下拉里选 1.7B → 现有 stt_switch 流程跑 → cache 命中秒下,只剩 ~4s session load
6. **手动 rm 检测**:在程序运行时 `rm -rf .../model_1.7B/` → 刷新设置页 → 状态变"未下载",下拉里 1.7B 重新 disabled
7. **i18n**:三语切换看每个 key 都有翻译

测试套:`uv run pytest`(全过 + 覆盖率不退化)。`uv run pytest tests/test_download_manager.py -v` 单跑新测试。

---

## 启动时 variant 未下载兜底(本轮顺带)

场景:用户配置 `qwen3.variant=1.7B`、本机只有 0.6B → 启动 `preload_model()` → `Qwen3ASRSTT("1.7B").load()` → 触发 2.4 GB 下载,期间程序看似启动成功但**首次按热键卡 5-10 分钟**,跟主诉求里的痛点症状完全一样。

**实现**(加在 `__main__.preload_model()` 调用前,~10 行):

```python
configured = config.get("qwen3", {}).get("variant", "0.6B")
if not download_manager.is_variant_downloaded(configured):
    logger.warning(
        "configured_variant_not_downloaded",
        configured=configured,
        message="Configured variant not in cache; falling back to 0.6B for preload",
    )
    if download_manager.is_variant_downloaded("0.6B"):
        wi.stt = Qwen3ASRSTT(variant="0.6B")  # 临时切引擎(不改 config 持久化值)
    else:
        # 0.6B 也没下:跳过 preload,让用户进设置页主动下
        logger.error("no_variant_downloaded_skip_preload")
        args.no_preload = True
```

测试:加一条 `test_main_fallback_when_configured_variant_missing`,在 `tests/test_main_*.py` 里(具体文件实施时定),mock `is_variant_downloaded` 返 False,断言 `wi.stt.variant == "0.6B"`。

不在 scope:不自动触发"启动时立刻 DownloadManager.start(配置的 variant)",理由是用户可能就是临时换机器没下,自动下 2.4GB 比较粗暴 —— 留给用户进设置页主动决定。

---

## Critical Files

- `src/daobidao/stt/qwen3/_download_manager.py` (新建,~250 行)
- `src/daobidao/settings_server.py` (4 个新端点 handler + 构造器加参数)
- `src/daobidao/__main__.py` (实例化 DownloadManager + 注入 SettingsServer)
- `src/daobidao/assets/settings.html` (新卡片 + 进度条 CSS + JS poll + 下拉联动)
- `src/daobidao/assets/locales/{zh,en,fr}.json` (13 条新 key)
- `tests/test_download_manager.py` (新建)
- `tests/test_settings_server_models.py` (新建)
