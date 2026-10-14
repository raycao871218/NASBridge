# NASBridge

NASBridge 包含 NAS 管理面板和独立的运维脚本：面板用于日常查看与管理，脚本可通过 Shell、任务计划或 OpenClaw/Codex skill 执行。两者的配置、依赖和执行入口分别维护。

## 从哪里开始

- **部署或更新 NAS 网页面板**：[Docker 面板说明](docker/nasbridge/README.md)。
- **本地修改 → GitHub → NAS 拉取部署**：[开发与更新流程](docker/nasbridge/README.md#日常开发与更新部署)。
- **使用 OpenClaw/Codex 编排证书任务**：[SKILL.md](SKILL.md) 与 [运维手册](references/ops-playbook.md)。
- **配置 ZeroTier 网络**：[部署与排错指南](zerotier.md)。

## 当前功能与入口

| 入口 | 已实现的功能 | 执行边界 |
| --- | --- | --- |
| Docker 网页面板 | Mihomo 状态、连接、配置和指定容器启停；证书检查与同步；Nginx 反代管理；阿里云安全组；ZeroTier 成员管理；每日任务结果；短信归档与验证码复制 | 使用独立容器、数据库与宿主机挂载配置，详见面板 README |
| 移动端 HTTP API | 文章列表/全文/已读状态，视频目录/缩略图/Range 播放，短信搜索和验证码提取，手动 Google 连通性检测 | Android 工程另行维护；本仓库提供后端接口 |
| 证书编排脚本 | NAS 申请/部署 → 拉到执行机 → 同步到远端 → reload Nginx | 通过 SSH/rsync 调用；默认有 15 天签发冷却判断 |
| 网络与证书检查脚本 | NAS/服务器可达性、证书有效期、按配置发送邮件或 Telegram 通知 | 独立执行，需要配置通知凭据；不会由面板自动调度 |
| hosts 与路由器脚本 | 按 NAS/OpenWrt 优先级更新 hosts、更新 GitHub hosts 数据、更新 IPv6 防火墙地址 | 修改执行机器的网络配置，需对应权限与环境 |
| 影视元数据脚本 | 从 TMDB 补全 NFO、海报和背景图，支持手动 ID 纠偏 | 独立扫描媒体目录，不是网页功能或视频下载器 |

服务器候选地址选择与 hosts 更新采用配置优先级和可达性判断，不是按延迟、带宽或负载选出“最优节点”的负载均衡。ZeroTier/公网反代的实际速度取决于链路、直连或中继状态及带宽，仓库不保证固定速度排序。

仓库保留 `src/notify/` 的邮件和 Telegram 通知代码；这是运维脚本使用的通知能力。Docker 面板和 Android 后端没有内置手机推送通道。

## 目录与配置

```text
docker/nasbridge/  网页面板、移动端 API、Docker 部署模板与测试
src/              证书、反代、网络、hosts、通知及 TMDB 脚本
scripts/          证书编排统一入口
references/       环境变量映射、主机边界、运维手册与纠偏示例
SKILL.md          OpenClaw/Codex skill 说明
agents/           skill UI 元数据
```

根目录 `.env.example` 用于独立运维脚本，`domains_config.yaml.example` 用于域名与服务清单；`docker/nasbridge/.env.example` 和 `config/config.yaml.example` 用于 Docker 面板。实际配置均在部署机器上填写，不提交密码、私钥、个人地址或运行数据。

## 独立脚本环境

以下命令在仓库根目录执行。建议使用 Python 3.10+ 的独立虚拟环境：

```sh
python3 -m venv venv
. venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
cp domains_config.yaml.example domains_config.yaml
```

先编辑实际配置，再运行相应脚本。根目录依赖清单尚未列入 `src/check_ssl_expiry.py` 导入的 `cryptography`，使用该脚本时需额外安装：

```sh
python -m pip install cryptography
```

证书编排还需要 Bash、SSH、rsync，以及 NAS 上配置好的 acme.sh 容器和远端 Nginx。Nginx/Caddy 生成脚本需要目标软件、配置目录写权限和 reload 权限。脚本不是默认常驻服务，需要重复执行时由任务计划或外部调度器触发。

### 推荐的证书编排入口

在执行机配置根目录 `.env`，变量说明见 [环境映射](references/env-mapping.md)，主机职责见 [目标边界](references/target-boundary.md)。先预览，再执行真实操作：

```sh
bash scripts/run_cert_flow.sh --dry-run
# 确认 SSH 主机、源/目标目录和 reload 命令后执行：
bash scripts/run_cert_flow.sh
```

入口支持 `--env <path>` 和 `--skip-nas-update`。签发冷却由 `CERT_ISSUE_COOLDOWN_DAYS` 控制，默认 15 天；跳过签发时仍会同步现有证书并 reload Nginx。证书是否按期更新还取决于外部调度，脚本本身不会自动创建计划任务。历史脚本 `src/nas_sync_cert.sh` 保留在仓库，新的跨主机流程优先使用上述编排入口。

### 其他脚本

| 脚本 | 行为 |
| --- | --- |
| `src/nas_cert_update.sh` | 在 NAS 调用 acme.sh 容器申请证书并部署到群晖 DSM，由编排入口远程调用 |
| `src/check_ssl_expiry.py` | 读取根目录域名清单，检查证书，并按脚本逻辑发送邮件/Telegram 通知 |
| `src/nas_ping_test.py` | 检查 NAS 网络与配置的服务端口，生成 Telegram 报告 |
| `src/server_ping_test.py` | 检查候选地址，按优先级调整 Nginx 后端，并在需要时 reload/通知 |
| `src/server_load_proxy_from_config.py` | 读取根目录 `domains_config.yaml`，按 `PROXY_SERVER_TYPE` 调用 Nginx/Caddy 生成器并 reload；当前入口不读取旧 `services.conf` |
| `src/server_create_nginx_proxy.py`、`src/server_create_caddy_proxy.py` | 按 `port usage` 位置参数与可选参数生成对应代理配置 |
| `src/hosts_update.py` | 根据 NAS/OpenWrt 可达性和优先级更新执行机 hosts |
| `src/router_hosts_update.py` | 获取 GitHub hosts 数据，更新指定 hosts 文件的对应区段 |
| `src/router_fw_nax_prefix.py` | 按环境变量更新路由器 IPv6 防火墙地址并执行配置的重启命令 |

例如，在完成相关配置和权限检查后运行代理配置加载器；查看单站点生成器的参数：

```sh
python3 src/server_load_proxy_from_config.py
python3 src/server_create_nginx_proxy.py --help
python3 src/server_create_caddy_proxy.py --help
```

反代脚本和网页反代管理是不同入口：独立脚本支持 Nginx/Caddy；当前网页管理实现针对远端 Nginx。执行网络和证书检查脚本可能发送真实通知，写入型脚本可能修改 hosts、防火墙、证书或代理配置，使用前应核对目标环境。

## TMDB 影视元数据同步脚本

脚本：`src/tmdb_media_sync.py`

能力：
- 扫描影视库目录，先检查资源是否完整（默认 `nfo + poster.jpg + fanart.jpg`）
- 资源完整则跳过，不重复抓取
- 资源缺失时，从 TMDB 抓取元数据和图片并写入 NFO
- 支持手动指定 `imdb_id` / `tmdb_id`，用于纠正搜索匹配错误

### 依赖

项目已包含依赖：`requests`

### 使用示例

1) 扫描整个影视库（仅顶层目录）

```bash
export TMDB_API_KEY="你的tmdb_api_key"
python3 src/tmdb_media_sync.py --library-root /volume1/video/Movies
```

2) 递归扫描

```bash
python3 src/tmdb_media_sync.py --library-root /volume1/video --recursive
```

3) 只处理单个目录，并强制指定 `tmdb_id`

```bash
python3 src/tmdb_media_sync.py \
  --library-root /volume1/video/Movies \
  --item-path "/volume1/video/Movies/Inception (2010)" \
  --media-type movie \
  --tmdb-id 27205
```

4) 只处理单个目录，并强制指定 `imdb_id`

```bash
python3 src/tmdb_media_sync.py \
  --library-root /volume1/video/TV \
  --item-path "/volume1/video/TV/Severance (2022)" \
  --media-type tv \
  --imdb-id tt11280740
```

5) 批量指定纠偏映射（按目录名或绝对路径）

```bash
python3 src/tmdb_media_sync.py \
  --library-root /volume1/video \
  --recursive \
  --override-file references/tmdb_override_example.json
```

### 参数说明

- `--library-root`：影视库根目录（必填）
- `--api-key`：TMDB API Key（可用环境变量 `TMDB_API_KEY`）
- `--recursive`：递归发现有视频文件的目录
- `--media-type`：`auto|movie|tv`（默认 `auto`）
- `--item-path`：只处理某个目录
- `--imdb-id` / `--tmdb-id`：单目录强制 ID
- `--override-file`：批量纠偏映射 JSON 文件
- `--overwrite-nfo`：已有 NFO 也重写
- `--overwrite-images`：已有图片也重下
- `--dry-run`：只打印计划动作，不落盘
