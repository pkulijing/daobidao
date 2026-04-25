# whisper-input (renamed)

> **本包已改名为 [`daobidao`](https://pypi.org/project/daobidao/)（叨逼叨）。**

## 为什么改名

`whisper-input` 这个名字辨识度太低,而且容易让人误以为我们用的是 OpenAI Whisper
模型 —— 实际跑的是阿里云的 Qwen3-ASR。改名为 `daobidao`(叨逼叨,中文口语里
"说个不停"的意思)之后,中文用户群体一眼就能 get 这是个语音输入工具。

## 现在该怎么用

```bash
# 推荐:直接安装新包
uv tool install daobidao

# 或者老命令也仍然可用(自动透传到新包,只多一行迁移提示)
uv tool install whisper-input
```

老的 `whisper-input` 包从 0.9.0 起仅作为 shim 转发到 `daobidao`,**所有未来更新
都发布到 daobidao**。本 shim 包不会再加新功能,只保留兼容性。

## 长期建议

```bash
# 升级到新包,卸载老 shim
pip install -U daobidao
pip uninstall whisper-input
```

详见 [github.com/pkulijing/daobidao](https://github.com/pkulijing/daobidao)。
