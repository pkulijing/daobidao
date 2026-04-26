# 需求:更新检测加 TTL

## 现状

`UpdateChecker.snapshot` 只在 `checked_at is None`(从未检查过)时由
`/api/update/check` 触发 `trigger_async()`。一次成功后 `checked_at` 写满,
之后无论用户怎么打开设置页都返回那次的缓存,**永不再查 PyPI**,直到进程
重启。

具体后果(刚验证过):

1. 用户装了 `daobidao 1.0.1`,启动时 server start 触发一次检查 →
   `latest=1.0.1`,缓存
2. 几小时后我们发了 `1.0.3` 到 PyPI
3. 用户打开设置页 → 拿回的还是缓存的 `latest=1.0.1, has_update=False`
4. 用户必须手动重启 daobidao 进程才能看到「可升级」横幅

## 期望

1. **TTL 自动刷新**(主路径):设置页打开时,如果上次检查超过一定时间(TTL),
   自动重新拉一次 PyPI;TTL 内的缓存继续直接返回
2. **「立即检查」按钮**(power-user 路径):在「高级设置」section 加一个手动
   触发按钮,绕过 TTL 强制重查。普通用户不必知道它存在,但用户(开发者
   / debug 场景)能用它快速拿最新结果

## 顺手捎带:terminal log 静默(不上 DEVTREE)

命令行启动 `daobidao` 时,terminal 持续打 INFO log 比较吵。文件 log 已经
完整落盘(`~/.local/state/daobidao/daobidao.log` / `~/Library/Logs/Daobidao/`
/ dev 模式 `logs/`),terminal 里其实不需要重复一份。

期望:**默认 terminal 不打 log**,加 `-v` / `--verbose` 显式开。这是个小
体验改善,跟 TTL 同 round 顺手做,DEVTREE 不单列一个节点。

## 不做

- 不做后台 timer 主动刷(对绝大多数用户而言每天检查一次不值得起常驻定时
  线程,UpdateChecker 现在就这么轻量挺好)
- 不动 `apply_upgrade` / 升级流程(没坏)
