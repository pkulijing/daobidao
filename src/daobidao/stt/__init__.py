"""STT 后端抽象包。

当前引擎:
  - qwen3: Qwen3-ASR int8 ONNX (0.6B / 1.7B 两款 variant),默认 0.6B

**懒加载原则**:本包的 __init__.py 刻意不做任何 eager 导入 —— numpy /
onnxruntime / modelscope / tokenizers 的 import 成本留给真正需要推理时再
付,让 `daobidao --help` 之类的轻量调用路径保持启动毫秒级。
"""

from daobidao.stt.base import BaseSTT


def create_stt(engine: str, config: dict) -> BaseSTT:
    """根据 engine 名称和配置创建 STT 实例。"""
    if engine == "qwen3":
        # 延迟 import:只有真正需要推理时才触发 numpy/onnxruntime 加载
        from daobidao.stt.qwen3 import Qwen3ASRSTT

        variant = config.get("variant", "0.6B")
        return Qwen3ASRSTT(variant=variant)
    from daobidao.i18n import t

    raise ValueError(t("stt.unknown_engine", engine=engine))


__all__ = ["BaseSTT", "create_stt"]
