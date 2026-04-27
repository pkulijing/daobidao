# 36 轮需求 — 模型管理与可视化下载

## 背景

26 轮上线了"识别模型"下拉(0.6B / 1.7B)+ 热切换,但只照顾了"两个模型都已下载"的稳定态。当前实际用户路径有两个明显的坑:

1. **第一次 init 时只下了默认的 0.6B**,1.7B 模型从来没下过 —— 用户在设置页选 1.7B 那一刻才会触发后台串行 `snapshot_download`,中国带宽下一次拉 ~2.4 GB,5-10 分钟起步。设置页此时唯一反馈是"切换中..."的 toast,**用户看不到下载进度、不知道还要多久、不能取消**,容易误判程序卡死把它强杀,结果 cache 半成品下次再 load 会出问题
2. **没有"先下模型再用"的入口** —— 想提前把 1.7B 备好,只能硬切过去等

## 核心诉求

设置页里**新增一个"模型管理"区域**,围绕"模型下载状态"这件事做透明化:

1. **可视化每个 variant 的下载状态**:对 0.6B / 1.7B 分别显示"已下载 / 未下载 / 下载中 X%"
2. **切换器只能切到已下载的模型**:"识别模型"下拉里**未下载的 variant 是 disabled** 的,选不动
3. **模型管理区域里能主动下载**:每个未下载的 variant 旁边有"下载此模型"按钮,点了才开始下,**有可视化进度条**(进度 / 大小 / 速度,允许取消)
4. **下载完成后自动刷态**:进度条消失 → 下拉里那个 variant 自动 enable → 用户可以正常切换到它

## 已 spike 确认的技术前提

### 1. modelscope 原生支持下载进度回调

`modelscope.snapshot_download` 暴露 `progress_callbacks: List[Type[ProgressCallback]]` 参数,源码 [.venv/.../modelscope/hub/callback.py](.venv/lib/python3.12/site-packages/modelscope/hub/callback.py):

```python
class ProgressCallback:
    def __init__(self, filename: str, file_size: int):  # 每个文件实例化一次,给文件名 + 总字节
        ...
    def update(self, size: int):  # 每收一块流就报告字节数
        ...
    def end(self):  # 该文件下完
        ...
```

写一个继承 `ProgressCallback` 的子类,在 `update` 里把字节累加到 `DownloadManager` 的 state 里(per-file + total + EMA 算速度),然后 `snapshot_download(..., progress_callbacks=[MyCallback])` 即可。**不需要自己绕开 modelscope 包 HTTP**,BACKLOG 里写的"如果不暴露,scope 再翻 50%"风险消解。

### 2. modelscope 原生支持 cache 检查

`modelscope.hub.file_download.ModelFileSystemCache` 是 modelscope 官方的 cache 索引类:每个 model_id 在 cache 目录下有一个 `.mcs` 索引文件记录 cached files 列表。关键方法 `get_file_by_path(file_path)` 检查指定文件是否在 cache 里;源码里它**自带磁盘一致性兜底**:

```python
def get_file_by_path(self, file_path):
    for cached_file in self.cached_files:
        if file_path == cached_file['Path']:
            cached_file_path = os.path.join(self.cache_root_location, ...)
            if os.path.exists(cached_file_path):
                return cached_file_path
            else:
                self.remove_key(cached_file)  # 文件被手动删了 → 自动清掉索引
    return None
```

也就是说**用户手动 `rm` cache 里的某个 .onnx 文件后,我们的 `is_variant_downloaded()` 检查会自动报 False**,不会出现"索引说有但磁盘没有"的状态错配。检查代码大致这样:

```python
from modelscope.hub.file_download import ModelFileSystemCache, get_model_cache_root

def is_variant_downloaded(variant: str) -> bool:
    cache = ModelFileSystemCache(
        get_model_cache_root(),
        owner="zengshuishui", name="Qwen3-ASR-onnx",
    )
    required = [
        f"model_{variant}/conv_frontend.onnx",
        f"model_{variant}/encoder.int8.onnx",
        f"model_{variant}/decoder.int8.onnx",
        # + tokenizer 那几个文件,具体名单 PLAN 阶段确定
    ]
    return all(cache.get_file_by_path(p) is not None for p in required)
```

### 3. 架构取向:DownloadManager 直接调 modelscope,不走 load()

潜在的反向取向疑虑:30 轮专门删掉了 `_downloader.py` 包装类,把 `snapshot_download` 直接 inline 进 `Qwen3ASRSTT.load()`,本轮新增 DownloadManager 是不是又把"下载"和"加载"拆回去了?

答案是**不矛盾**。30 轮反对的是"包装抽象层"——`_downloader.py` 给 modelscope 套一层我们自己的 API(`download(variant)` / `cache_root_for(variant)` 等),让 load() 不能直接用 modelscope。本轮的做法是:

```
用户主动点"下载 1.7B" → DownloadManager.start("1.7B")
                       → 后台线程跑 modelscope.snapshot_download(
                             ..., allow_patterns=[model_1.7B/*],
                             progress_callbacks=[ProgressTracker])
                       → 完成后 cache 里多了 model_1.7B/

用户后来切到 1.7B → WhisperInput._switch_stt_variant("1.7B")
                  → 后台线程跑 Qwen3ASRSTT("1.7B").load()
                  → load() 内部调 snapshot_download → 命中 cache(已下) → 跳过下载
                  → 只剩 ONNX session 构造 + warmup(~4s)
```

`DownloadManager.start()` 和 `Qwen3ASRSTT.load()` **平级地各自调用同一个底层 modelscope API**,没有"我们自己的下载抽象层",跟 30 轮精神一致。

为什么不让 DownloadManager 内部直接调 `Qwen3ASRSTT(variant).load()`(另一个备选):`load()` 除了下还会构造 ONNX runner + tokenizer + warmup(~4s + 占内存),用户点"下载"但还没切 variant 时这些是浪费。所以 DownloadManager 只负责把文件下到磁盘,session 构造留给真正切到该 variant 时。

## 不在本轮 scope

- **后台静默预下载**:不做"程序启动后自动把所有 variant 都拉下来"这种行为,只在用户**主动点下载按钮**时才下
- **断点续传**:第一版不做,中断就从头来。modelscope cache 里的半成品交给 modelscope 自己处理(下次启动如果识别到损坏会重拉)
- **`--init --variant 1.7B` 命令行支持**:可以顺手加,但不阻塞主流程

## 验收

- 设置页打开 → 模型管理区域显示两个 variant,各自的"已下载 / 未下载"状态正确
- 假设当前 0.6B 已下、1.7B 未下:
  - "识别模型"下拉里 1.7B 是 disabled,鼠标悬停有 hint 说"未下载"
  - 模型管理那里 1.7B 旁边有"下载此模型"按钮,点击后开始下,显示进度条 / 速度 / 已下 MB / 总 MB
  - 下载过程中可以点"取消",取消后状态回到"未下载",cache 里的不完整文件交给 modelscope 后续处理
  - 下载完成 → 按钮消失 → 1.7B 在下拉里 enable → 用户可以无网络等待地切过去(只剩 ~4s session load)
- 假设两个模型都已下载:
  - 模型管理区域两个都标"已下载",没有按钮
  - "识别模型"下拉两个都 enable,行为跟现在一样
- **手动删 cache 兼容**:用户在外部 `rm -rf ~/.cache/modelscope/hub/.../model_1.7B/` 之后,设置页一刷新,模型管理区域应自动把 1.7B 状态切回"未下载",下拉里 1.7B 自动 disabled

## 测试方法

测试机当前状态:0.6B 和 1.7B **都已下载**。要测"未下载 / 下载中 / 下载完成"完整流程,需要先把目标 variant 的 cache 清掉:

```bash
# 清 1.7B(保留 0.6B 和 tokenizer,这样程序仍可正常用 0.6B 录音):
rm -rf ~/.cache/modelscope/hub/models/zengshuishui/Qwen3-ASR-onnx/model_1.7B/
```

然后 daobidao 重启 → 进设置页 → 应该看到 1.7B 标"未下载" + 下拉里 1.7B disabled → 点"下载 1.7B" → 看进度条流动 → 下完 → 自动 enable。

要测"两个都未下载"边界场景:整个 model 目录清掉(`rm -rf .../Qwen3-ASR-onnx/model_*`,保留 tokenizer 比较安全;或全删让 modelscope 重新拉 tokenizer 也行)。
