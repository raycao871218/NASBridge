# Codex CLI 账号管理

通过鸟巢内网地址打开「Codex 账号」，或在麻雀会打开「服务 → Codex 账号」，页面会直接显示 Ubuntu 当前 CLI 登录和 CC Switch 保存的可切换邮箱。确认切换后，退出并重新启动该服务器上的 Codex。

在 `lan_hosts` 列出客户端直连鸟巢时使用的内网地址和端口。服务会拒绝其他 Host 及带转发标头的请求。NAS 的直连端口应只对可信网络开放，公网反向代理应封锁 `/api/codex-accounts` 路径。两个客户端同时操作时，服务器会拒绝并发切换或基于旧状态的请求。

在 Ubuntu 上以实际使用 Codex 的用户安装 `codex-account.py` 到 `~/.local/lib/nasbridge/`。它默认读取 `~/.codex/auth.json`（或服务器配置的 `CODEX_HOME`）及 `~/.cc-switch/codex_oauth_auth.json`。OAuth 刷新默认走 Ubuntu 的本地代理 `http://127.0.0.1:7890`，可在服务器端指定 `NASBRIDGE_CODEX_HTTPS_PROXY`，空值禁用代理。账号登录失效时，先在 Ubuntu 重新授权再切换。

为 NASBridge 生成专用 Ed25519 密钥，在 Ubuntu 的 `authorized_keys` 使用以下限制（替换绝对路径）：

```text
restrict,command="/usr/bin/python3 /home/task-user/.local/lib/nasbridge/codex-account.py" ssh-ed25519 <public-key>
```

通过已验证的 SSH 连接核对 Ubuntu 主机公钥，将私钥和 known_hosts 保存在 NAS 的配置目录，私钥权限为 0600。在实际 `config.yaml` 添加 `codex_accounts`（见示例）。

`GET /api/codex-accounts` 返回 `current` 和 `accounts`。`POST /api/codex-accounts/switch` 接受 `{"target":"…","expected_current":"…"}` 并附加返回 `changed`。切换失败后先刷新确认状态，再决定是否重试。

要启用「设备管理」的手动关机，把 `shutdown/manual-poweroff.py` 安装到 Ubuntu 上的 `codex-account.py` 同一目录，并在 NAS 的 `codex_accounts` 下设定 `manual_poweroff: true`。Home Assistant 加载 `shutdown/manual-poweroff-automation.yaml` 中的自动化，使用独立的随机 webhook ID，并调用现有的 `script.safe_poweroff_ubuntu_server`。将完整 webhook 地址保存在 Ubuntu 同目录的 `manual-poweroff-webhook` 文件（权限 0600）。每日任务运行中、等待重试或系统阻止关机时，执行端拒绝请求。Home Assistant 负责正常关机并在确认离线后关闭插座。用 `GET /api/codex-accounts/power` 验证连接和状态。
