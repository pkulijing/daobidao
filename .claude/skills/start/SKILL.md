---
name: start
description: 开始一个新的开发项：创建文档目录，撰写 PROMPT.md 和 PLAN.md，确认后再开始写代码
disable-model-invocation: true
---

用户调用此 skill 表示要开始一个新的开发项。参数为用户的需求描述。

按照全局 CLAUDE.md 中的开发模式，严格遵循「执行前必须先完成 PROMPT.md 和 PLAN.md 的撰写并确认，再开始写代码」：

1. 在 `docs/` 下创建新的开发项文件夹（数字递增 + 中文描述）
2. 撰写 `PROMPT.md`（基于用户提供的参数）
3. 进入计划模式，撰写 `PLAN.md` 并请用户确认
4. 确认后再开始写代码
