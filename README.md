# 内网穿透通知机器人

一个专为群晖 NAS 和服务器内网穿透设计的通知系统，可通过 Telegram 和邮件实时监控和通知内网穿透状态。

## 主要功能

- 支持 Telegram 机器人和邮件双通道通知
- 实时监控内网穿透状态
- 自动检测服务连接状态
- 支持发送系统日志和错误报告
- 支持多接收者配置
- 支持 HTML 格式邮件通知
- 适配群晖 DSM 系统和常见 Linux 服务器

## 使用场景

- 群晖 NAS 内网穿透状态监控
- 服务器端口转发状态检测
- 系统运行状态通知
- 关键服务可用性监控
- 安全事件实时预警

## 安装说明

1. 克隆仓库：
```bash
git clone <repository_url>
cd notify_bots
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 配置环境变量：
```bash
cp env.example .env
```

## 配置指南

### Telegram 机器人配置
1. 在 Telegram 中找到 [@BotFather](https://t.me/botfather)
2. 创建新机器人，获取 `TELEGRAM_BOT_TOKEN`
3. 获取你的 Telegram 用户 ID（可通过 [@userinfobot](https://t.me/userinfobot) 获取）
4. 在 `.env` 文件中设置：
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_USER_IDS=your_user_id_here
```

### 邮件通知配置（以 QQ 邮箱为例）
1. 登录 QQ 邮箱网页版
2. 开启 SMTP 服务并获取授权码
3. 在 `.env` 文件中设置：
```
SMTP_SERVER=smtp.qq.com
SMTP_PORT=587
SMTP_USERNAME=your_qq_number@qq.com
SMTP_PASSWORD=your_qq_email_auth_code
EMAIL_SENDER=your_qq_number@qq.com
EMAIL_RECEIVERS=receiver1@example.com,receiver2@example.com
```

### 日志目录配置
```
LOG_DIR=/path/to/your/logs
```

## 使用方法

### Telegram 通知

1. 发送状态通知：
```bash
python telegram_notify.py "内网穿透状态：正常运行中"
```

2. 发送今日监控日志：
```bash
python telegram_notify.py --log
```

3. 发送指定日期的监控记录：
```bash
python telegram_notify.py --log 2024-03-20
```

### 邮件通知

1. 发送状态报告：
```bash
python email_notify.py "服务器端口转发状态报告"
```

2. 发送今日监控日志：
```bash
python email_notify.py --log
```

3. 发送指定日期的监控记录：
```bash
python email_notify.py --log 2024-03-20
```

## 日志文件规范

- 日志文件必须使用 `.log` 扩展名
- 文件名需包含日期（格式：YYYY-MM-DD）
- 示例：`tunnel-2024-03-20.log`

## 常见问题解决

1. Telegram 通知失败
   - 检查 `TELEGRAM_BOT_TOKEN` 是否正确
   - 确保已经与机器人进行过对话
   - 检查网络连接和代理设置
   - 验证 Telegram API 是否可访问

2. 邮件发送失败
   - 检查 SMTP 配置是否正确
   - 确认授权码是否有效
   - QQ 邮箱需使用授权码而非登录密码
   - 发件人地址必须与 SMTP 用户名一致

3. 内网穿透监控问题
   - 确认监控脚本权限设置
   - 检查端口配置是否正确
   - 验证系统防火墙设置
   - 确保日志目录可写入

## 项目依赖

- python-dotenv：环境变量管理
- requests：HTTP 请求处理
- python-telegram-bot：Telegram 机器人 API

## 开源协议

MIT License

## 参与贡献

欢迎提交 Issue 和 Pull Request！如果你在使用过程中发现任何问题或有改进建议，请随时反馈。