# 每日任务手动执行

面板常驻显示“手动执行”，当天已有成功记录也可再次执行。执行中禁用按钮，SSH/数据库不可用时禁止提交。数据库事务锁串行处理手动请求；systemd 合并并发的计划和手动启动，运行中的服务不会被重启。

执行端必须已部署 `rhino-daily.service`。本功能复用其原有下载、转录、概要与数据库记录流程，通过 `systemctl start --no-block` 提交，不在 HTTP 请求中等待完整任务完成。失效的历史 `running` 记录不会阻止重试，以实际服务状态为准。

在执行端安装本目录 `daily-task-control.py`，例如安装为 `/home/task-user/.local/lib/nasbridge/daily-task-control.py`。为 NASBridge 生成独立 Ed25519 密钥，仅保存到本地忽略的 `config/`；将公钥加入执行用户的 `authorized_keys`，限制为：

```text
restrict,command="/usr/bin/python3 /home/task-user/.local/lib/nasbridge/daily-task-control.py" ssh-ed25519 <public-key>
```

该用户需要免交互执行 `sudo /usr/bin/systemctl start --no-block rhino-daily.service`。强制命令只接受 `status` 和 `start`，不接受服务名或任意 shell 命令。通过可信渠道核对并保存执行端主机公钥，不关闭主机密钥验证。

在现有本地 `config/config.yaml` 添加 `daily_task_control`，字段见示例配置。私钥、known_hosts、真实主机配置均不提交 Git。本功能不需要修改现有 Compose 或网络。

接口为同源 JSON `POST /api/daily-tasks/run`，请求体 `{}`；接受返回 202，并发提交/执行中返回 409，不可用返回 503。面板现有轮询会刷新进度；网络报错后先刷新记录确认，避免盲目重复提交。
