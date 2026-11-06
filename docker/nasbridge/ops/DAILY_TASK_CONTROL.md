# 每日任务手动执行

面板可选择 2026-10-08 起的日期，默认今天。选择往日会补抓该日公开视频，并补齐该日缺失的转写与概要。日期沿用视频库文件名中的 YouTube 上传日期。页面分别展示仍可读取的已归档视频数量和最近任务状态。只有针对所选日期完成的发现阶段明确返回零部时，才显示当天没有发现公开视频。

当天已有成功记录也可再次执行。执行中禁用按钮，SSH/数据库不可用时禁止提交。数据库事务锁串行处理手动请求，运行中的服务不会被重启。往日发现结果不会安排或取消今天的自动重试。

执行端必须已部署 `rhino-daily.service`。本功能复用其原有下载、转录、概要与数据库记录流程，通过 `systemctl start --no-block` 提交，不在 HTTP 请求中等待完整任务完成。失效的历史 `running` 记录不会阻止重试，以实际服务状态为准。

在执行端安装本目录 `daily-task-control.py`，例如安装为 `/home/task-user/.local/lib/nasbridge/daily-task-control.py`。为 NASBridge 生成独立 Ed25519 密钥，仅保存到本地忽略的 `config/`；将公钥加入执行用户的 `authorized_keys`，限制为：

```text
restrict,command="/usr/bin/python3 /home/task-user/.local/lib/nasbridge/daily-task-control.py" ssh-ed25519 <public-key>
```

该用户需要免交互执行 `sudo /usr/bin/systemctl start --no-block rhino-daily.service`。强制命令接受 `status`、`start` 和日期严格校验的 `start YYYY-MM-DD`。往日日期通过任务目录下 `state/backfill.date` 一次性交给原服务，控制器与 runner 使用同一把文件锁。通过可信渠道核对并保存执行端主机公钥。

在现有本地 `config/config.yaml` 添加 `daily_task_control`，字段见示例配置。私钥、known_hosts、真实主机配置均不提交 Git。本功能不需要修改现有 Compose 或网络。

同源 JSON 接口 `POST /api/daily-tasks/run` 接收 `{"date":"YYYY-MM-DD"}`，原请求体 `{}` 仍执行今日任务。接受返回 202，并发提交或执行中返回 409，不可用返回 503。通过 `GET /api/daily-tasks/day?date=YYYY-MM-DD` 查询归档和最近一次运行。面板现有轮询会刷新进度。

升级前先确认执行服务空闲并备份原脚本。先在任务目录同时更新 `rhino-run.py`、`rhino-vm.py`、`rhino-postprocess.py` 并安装 `rhino_backfill.py`，再更新 SSH 强制命令脚本，最后部署 NAS 面板。复用现有 systemd 服务、sudo 规则和 PostgreSQL 表。
