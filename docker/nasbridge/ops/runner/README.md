# 阶段进度与结束状态

新增 11 点重试及关机保护，见 [部署与行为说明](../shutdown/README.md)。`rhino_schedule.py` 是 runner 的必要依赖，须与四个原有 Python 文件一并安装；service/timer 模板也纳入本目录。

执行端原目录保留 `.venv`、`rhino_db.py`、数据库及 YouTube 本地凭据。将本目录四个 Python 文件部署到原任务目录；从原脚本添加结构化事件，概要与分析框架仍由原有一次 Codex 执行共同处理，没有声称能够区分这一次调用内部的细节。

先备份旧脚本并确认服务未运行。使用现有连接运行 `rhino_db.connect()` 创建原表，再执行 `rhino-run.py` 中的 `PROGRESS_SQL` 创建 `rhino_daily_progress` 表；该表以 `run_id` 关联运行记录，不修改现有数据格式。在 systemd 原服务添加 `progress-stop.conf` drop-in，按本地任务路径调整 `ExecStopPost`，再 `systemctl daemon-reload`。不得覆盖原服务、timer、Compose 或凭据。安装新版 `../daily-task-control.py` 到原 SSH 强制命令位置。

每个阶段开始/完成/失败实时更新本地 JSON 和数据库，心跳每 15 秒更新。无新文件的阶段明确标记跳过。下载和转写提供实际进度；下载百分比属于当前视频/音轨，不是整个任务的总进度。概要阶段没有虚构百分比。

默认时限：检查目录 2 分钟，发现视频 6 分钟，下载 90 分钟，转写 90 分钟，概要及分析框架 2 小时，同步 5 分钟，保存 2 分钟；任务总时限 6 小时。下载和转写连续 10 分钟无有效进度也会终止整个子进程组并记录 `timed_out`。阶段时限不会因为心跳而延长。systemd 仍保留原 6 小时硬时限。

正常退出或 SIGTERM 会写入终态；SIGKILL/硬超时通过 `ExecStopPost` 补写。整机掉电没有机会执行清理，因此面板刷新时也核对 boot ID、systemd InvocationID、MainPID 和服务状态，修复所有残留 `running` 记录，包括隐藏/列表页以外记录。新实例替代旧实例、主机重启或确认进程已退出时写入 `interrupted`；systemd 明确超时则写入 `timed_out`。旧记录缺乏准确结束时间时，结束时间为检测到中断的时间，不推算实际断点。历史成功记录无阶段明细时保持原样，页面标注旧版记录。

连接失败显示 `unknown`（状态待确认），不持久化为死亡；进程仍存在但心跳超过 90 秒显示 `unresponsive`（心跳异常），不贸然重复启动。服务与心跳恢复后可自动恢复正常显示。心跳只证明监督器存活，不代表下载或 AI 工作有推进，因此另存最近进展时间，并由阶段/无进展时限处理挂起。

测试用独立临时目录、mock 数据库和真实短命子进程验证。运行 `python -m unittest discover -s tests -p 'test_*.py' -v` 时将整个 `ops` 目录只读挂载到 `/app/ops`；无需读取生产材料。前端运行 `node test_daily_progress_ui.cjs`。

## 观察记录与主框架

运行记录分别比较 NAS 上 `references/observations.md` 和 `SKILL.md` 的内容哈希，将 `observations_status`、`framework_status`、`review_status` 和中文判断原因保存到 `rhino_daily_progress.progress.skill_update`；无需修改私有 `rhino_db.py` 或已有数据库表结构。原有 `skill_updated` 仍表示主框架内容变化，兼容旧客户端。

有新概要时，原有 Codex 执行额外输出一份临时 JSON 判断报告，说明新增证据为何改变或未改变主框架；读取并限制长度后删除临时报告。没有新原文则明确记录未进行框架复核。报告缺失、无效或复核中断时显示相应说明，不伪造具体原因。面板分别展示观察记录、主框架与判断原因；历史任务未采集的字段显示“未记录”，不根据概要数量推测观察日志已更新，也不回写旧记录。

部署时需同时更新执行端的 `rhino-run.py`、`rhino-postprocess.py` 和 NAS 面板。先确认每日服务未运行并备份脚本，再原子替换；无需重新执行当天的下载或 AI 分析，新任务开始采集详细更新记录。
