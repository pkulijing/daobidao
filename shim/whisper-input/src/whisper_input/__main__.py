"""老 console script 入口:打一行迁移提示后转发到 daobidao。"""

import sys


def main() -> None:
    sys.stderr.write(
        "[迁移提示] whisper-input 已改名为 daobidao。\n"
        "[迁移提示] 之后请用 `daobidao` 命令启动,旧的 `whisper-input` 仍可用,\n"
        "[迁移提示] 但只是转发到新包,新功能更新都会发布到 daobidao。\n"
        "[迁移提示] 长期建议: pip install -U daobidao && pip uninstall whisper-input\n"
    )
    sys.stderr.flush()

    from daobidao.__main__ import main as _real_main

    _real_main()


if __name__ == "__main__":
    main()
