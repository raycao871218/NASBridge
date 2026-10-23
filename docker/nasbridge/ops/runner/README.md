# 阶段进度与结束状态

新增 11 点重试及关机保护，见 [部署与行为说明](../shutdown/README.md)。`rhino_schedule.py` 是 runner 的必要依赖，须与四个原有 Python 文件一并安装；service/timer 模板也纳入本目录。

执行端原目录保留 `.venv`、`rhino_db.py`、数据库及 YouTube 本地凭据。将本目录四个 Python 文件部署到原任务目录；从原脚本添加结构化事件，概要与分析框架仍由原有一次 Codex 执行共同处理，没有声称能够区分这一次调用内部的细节。

先备份旧脚本并确认服务未运行。使用现有连接运行 `rhino_db.connect()` 创建原表，再执行 `rhino-run.py` 中的 `PROGRESS_SQL` 创建 `rhino_daily_progress` 表；该表以 `run_id` 关联运行记录，不修改现有数据格式。在 systemd 原服务添加 `progress-stop.conf` drop-in，按本地任务路径调整 `ExecStopPost`，再 `systemctl daemon-reload`。不得覆盖原服务、timer、Compose 或凭据。安装新版 `../daily-task-control.py` 到原 SSH 强制命令位置。

每个阶段开始/完成/失败实时更新本地 JSON 和数据库，心跳每 15 秒更新。无新文件的阶段明确标记跳过。下载和转写提供实际进度；下载百分比属于当前视频/音轨，不是整个任务的总进度。概要阶段没有虚构百分比。

默认时限：检查目录 2 分钟，发现视频 6 分钟，下载 90 分钟，转写 90 分钟，概要及分析框架 2 小时，同步 5 分钟，保存 2 分钟；任务总时限 6 小时。下载和转写连续 10 分钟无有效进度也会终止整个子进程组并记录 `timed_out`。阶段时限不会因为心跳而延长。systemd 仍保留原 6 小时硬时限。

正常退出或 SIGTERM 会写入终态；SIGKILL/硬超时通过 `ExecStopPost` 补写。整机掉电没有机会执行清理，因此面板刷新时也核对 boot ID、systemd InvocationID、MainPID 和服务状态，修复所有残留 `running` 记录，包括隐藏/列表页以外记录。新实例替代旧实例、主机重启或确认进程已退出时写入 `interrupted`；systemd 明确超时则写入 `timed_out`。旧记录缺乏准确结束时间时，结束时间为检测到中断的时间，不推算实际断点。历史成功记录无阶段明细时保持原样，页面标注旧版记录。

连接失败显示 `unknown`（状态待确认），不持久化为死亡；进程仍存在但心跳超过 90 秒显示 `unresponsive`（心跳异常），不贸然重复启动。服务与心跳恢复后可自动恢复正常显示。心跳只证明监督器存活，不代表下载或 AI 工作有推进，因此另存最近进展时间，并由阶段/无进展时限处理挂起。

测试用独立临时目录、mock 数据库和真实短命子进程验证。运行 `python -m unittest discover -s tests -p 'test_*.py' -v` 时将整个 `ops` 目录只读挂载到 `/app/ops`；无需读取生产材料。前端运行 `node test_daily_progress_ui.cjs`。
