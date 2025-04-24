# 通知机器人

一个简单但功能强大的通知系统，支持通过 Telegram 和邮件发送消息和日志文件。

## 功能特点

- 支持 Telegram 机器人通知
- 支持邮件通知（SMTP）
- 支持发送普通消息
- 支持按日期发送日志文件内容
- 支持多接收者
- 支持 HTML 格式邮件
- 自动检测服务连接状态

## 安装

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

## 配置

### Telegram 配置
1. 在 Telegram 中找到 [@BotFather](https://t.me/botfather)
2. 创建新机器人，获取 `TELEGRAM_BOT_TOKEN`
3. 获取你的 Telegram 用户 ID（可以通过 [@userinfobot](https://t.me/userinfobot) 获取）
4. 在 `.env` 文件中设置：
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_USER_IDS=your_user_id_here
```

### 邮件配置（以 QQ 邮箱为例）
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

1. 发送普通消息：
```bash
python telegram_notify.py "你的消息内容"
```

2. 发送今天的日志：
```bash
python telegram_notify.py --log
```

3. 发送指定日期的日志：
```bash
python telegram_notify.py --log 2024-03-20
```

### 邮件通知

1. 发送普通消息：
```bash
python email_notify.py "你的消息内容"
```

2. 发送今天的日志：
```bash
python email_notify.py --log
```

3. 发送指定日期的日志：
```bash
python email_notify.py --log 2024-03-20
```

## 日志文件要求

- 日志文件必须以 `.log` 结尾
- 文件名中需要包含日期（格式：YYYY-MM-DD）
- 例如：`app-2024-03-20.log`

## 常见问题

1. Telegram 发送失败
   - 检查 `TELEGRAM_BOT_TOKEN` 是否正确
   - 确保已经与机器人进行过对话
   - 检查网络连接是否正常

2. 邮件发送失败
   - 检查 SMTP 配置是否正确
   - 确认授权码是否有效
   - QQ 邮箱需要使用授权码而不是密码
   - 发件人地址必须与 SMTP 用户名一致

3. 找不到日志文件
   - 检查 `LOG_DIR` 路径是否正确
   - 确认日志文件名格式是否包含日期
   - 验证文件扩展名是否为 `.log`

## 依赖

- python-dotenv
- requests
- python-telegram-bot

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！ 