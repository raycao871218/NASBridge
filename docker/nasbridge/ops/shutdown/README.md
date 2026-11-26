# 每日任务重试与自动关机保护

执行端为 `ubuntu-server`。原有定时任务在周二至周六北京时间 10:00 启动 `rhino-daily.service`；新增 `rhino-retry.timer` 在 11:00 唤起重试控制器。仅当 10 点这一轮成功发现 **0 个待下载视频** 时，写入 `state/retry.json` 的 `waiting` 状态。发现接口报错不等同于无更新，不自动安排这次重试。

11 点时若首轮尚在执行，控制器等它结束后再检查是否仍需重试；最多重试一次，执行前先持久化 `attempted_at`。11 点重试仍无更新不再安排第三次。手动运行发现了新视频会取消尚未执行的等待重试。重启后由重试 timer 的 `OnBootSec=2min` 检查已经到期的等待记录，过期记录不会直接丢弃。

鸟巢“每日任务”页通过受限 SSH 控制器读取真实重试状态，展示等待、重试执行中、结束/失败、取消及无安排状态；等待时显示北京时间和基于执行端时间估算的剩余分钟数。关机保护根据每日服务、重试服务与等待记录计算，读取失败明确显示待确认，不误报保护已解除。页面跟随现有 15 秒轮询更新。

关机监视器在采样之前检查每日服务、重试服务及持久化重试状态。处于 `waiting`、`retrying` 或服务 active/activating/reloading/deactivating 时记录 `PROTECTED`，跳过负载检测和关机请求，并重置连续空闲计时。服务/状态读取失败也保守地禁止自动关机。在最终向 HA 请求关机前再次复核，防止采样过程中任务开始后仍使用旧判断。

每日服务的 `inhibit-shutdown.conf` 还使用 systemd shutdown inhibitor 包住整个任务（包括手动启动），抵御常规系统关机请求。`sudo` 仅用于申请 inhibitor，实际 Python 任务仍以 caobowen 执行并保留 `INVOCATION_ID`。强制关机、断电或绕过 inhibitor 的命令不在软件保护范围内。

`shutdown-readiness-monitor.py` 沿用原有空闲阈值、检测时段和 HA 通知流程。真实 webhook 从权限为 0600 的 `/etc/nasbridge-shutdown.env` 读取：`NASBRIDGE_SHUTDOWN_WEBHOOK=...`；不提交 Git。`environment.conf` 安装在 `shutdown-readiness-monitor.service.d/`。缺少 webhook 配置时不发送关机请求。手动验证务必使用 `--dry-run`，该参数禁止调用真实 webhook。

部署前备份执行脚本与 unit；先安装 `rhino_schedule.py`，再原子替换 runner、视频脚本和关机监视器，安装 retry service/timer 及两个 drop-in，然后 `systemctl daemon-reload` 并启用 `rhino-retry.timer`。配置 reload 不重启正在执行的每日任务；inhibitor 从下一次任务启动起生效。已有运行任务由服务状态检查保护。

测试：`python -m unittest discover -s tests -p test_retry_shutdown.py -v`。测试使用临时状态文件和 mock systemctl/webhook，不执行真实下载、关机或写入生产状态。
