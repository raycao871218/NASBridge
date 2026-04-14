# NASBridge OpenClaw Skill

本仓库已调整为 **skill-first** 结构，可直接作为 OpenClaw/Codex skill 使用。

## Skill 入口

- `SKILL.md`：skill 主说明与触发范围
- `agents/openai.yaml`：skill UI 元数据
- `scripts/run_cert_flow.sh`：统一证书编排入口（OpenClaw 执行）
- `references/`：环境变量映射、主机边界、运维手册

## 快速运行

```bash
# 先模拟执行
scripts/run_cert_flow.sh --dry-run

# 再真实执行
scripts/run_cert_flow.sh
```

该流程会执行：
1. OpenClaw SSH 到 NAS 更新证书
2. 证书从 NAS 拉到 OpenClaw 本机
3. 证书从 OpenClaw 推到服务器
4. SSH 到服务器 reload nginx

# 这是一个用作我个人NAS有关内网穿透的实践

## 项目简介
本项目提供了一套完整的NAS远程访问解决方案，包含内网穿透和通知系统两大功能模块。通过ZeroTier实现安全的远程访问，支持多种部署方案以满足不同场景需求。

## 主要功能

### 🌐 网络访问
- **内网穿透**：通过 ZeroTier 实现 NAS 资源的远程安全访问
- **负载均衡**：自动切换最优可用的内网服务节点
- **反向代理**：自动化生成和管理 Nginx 配置

### 🔔 通知系统
- **多渠道推送**：支持邮件和 Telegram 通知
- **事件通知**：系统状态变更、证书更新等关键事件推送

### 路由器自动更新
- **添加host支持github**: 脚本定期获取[github520](https://github.com/521xueweihan/GitHub520)的数据源添加至hosts文件

### 📖 配置文档
- **服务器ZeroTier 配置指南**：详细的服务器端 ZeroTier 部署和故障排除说明 → [查看文档](zerotier.md)

### 📊 监控与测试
- **网络监控**：
  - NAS 网络连通性检测
  - ZeroTier 网络状态监控
  - 反向代理服务可用性测试
- **证书管理**：
  - SSL 证书有效期监控
  - 自动化证书更新提醒
  - 证书同步状态检查
- **智能域名解析**：
  - 自动检测 NAS 和 OpenWrt 连通性
  - 动态调整 hosts 文件中的域名解析
  - 优先级管理：NAS > OpenWrt > 注释

## 网络访问速度对比
| 访问方式 | 速度优先级 | 适用场景 |
|---------|----------|----------|
| 内网直连 | 最快 | 局域网内访问 |
| ZT网络 | 较快 | 远程私有访问 |
| 公网代理 | 较慢 | 公开访问 |

## 内网穿透方案

### 方案一：ZT私有网络（推荐）
适用于个人或家庭使用，通过ZeroTier实现安全的远程访问。

#### 优点
- 安全性高：基于ZeroTier的加密通信
- 速度快：带宽仅受限于家庭网络
- 成本低：无需公网服务器
- 配置简单：一次配置，长期稳定

#### 缺点
- 需要手动为每个设备安装ZT客户端
- 首次配置需要一定技术基础

#### 所需设备
- NAS设备（如群晖等）
- ZeroTier网络
- 需要访问NAS的终端设备

#### 部署步骤
1. NAS安装证书管理工具
   ```bash
   # 方式一：Docker命令安装
   docker pull neilpang/acme.sh
   
   # 方式二：通过NAS的Docker应用中心安装
   # 搜索并安装 neilpang/acme.sh
   ```

2. NAS配置反向代理服务器
   > 目的：实现HTTPS安全访问
   - 群晖配置路径：控制面板 -> 登录门户 -> 高级 -> 反向代理服务器
   - 示例：将HTTP 8123端口代理到HTTPS 8124
   - 特殊服务说明：
     * HomeAssistant等需要WebSocket支持的服务需要额外配置header
     * 具体配置参考各服务的官方文档

3. 配置SSL证书
   - 群晖配置路径：控制面板 -> 安全性 -> 证书
   - 确保证书正确安装并应用到相应的服务

4. 部署自动化脚本
   - 克隆本项目到NAS
   - 复制`.env.example`为`.env`并配置环境变量
   - 配置证书自动更新：
     * 脚本路径：`src/nas_cert_update.sh`
     * 执行权限：可能需要ROOT权限
     * 更新周期：建议60天
   - 群晖自动化配置：控制面板 -> 任务计划 -> 计划的任务 -> 用户定义脚本

5. ZeroTier网络配置
   - 注册并创建ZT网络
   - NAS安装ZT客户端（通过套件中心）
   - 终端设备安装ZT客户端
   - 将所有设备加入网络并在ZT控制面板授权

6. 连接测试
   - 通过ZT网络IP访问NAS
   - 验证HTTPS证书是否生效
   - 测试各项服务是否正常

#### 域名访问配置（可选）
1. DNS解析方案
   - 将域名解析到NAS的ZT网络IP
   - 适用于所有ZT网络内的设备

2. 路由器劫持方案（推荐）
   - 配置路由器的域名劫持功能
   - 内网访问：自动劫持到NAS内网IP
   - 外网访问：通过ZT网络IP访问
   > 注意：如果设备修改了hosts，需要先关闭才能使用内网直连

### 方案二：公网服务器代理
适用于需要公开访问或无法安装ZT客户端的场景。

#### 优点
- 无需客户端：可直接通过浏览器访问
- 随时可用：不依赖特定软件
- 公开访问：支持分享给他人

#### 缺点
- 带宽受限：取决于服务器带宽
- 安全风险：暴露在公网
- 配置复杂：需要更多安全措施
- 成本较高：需要维护服务器

#### 所需设备
- NAS设备
- 公网服务器
- ZeroTier网络（用于NAS与服务器通信）

#### 部署步骤
基础配置同方案一，额外步骤：

1. 在NAS中执行证书同步脚本
   ```bash
   # 方式一：使用项目提供的同步脚本
   src/nas_sync_cert.sh
   
   # 方式二：使用caddy/certbot（需单独维护证书）可跳过该步骤
   ```

2. 服务器配置
   - 安装并配置ZeroTier
   - 克隆本项目
   - 配置环境变量：复制`.env.example`为`.env`
   - 配置反向代理：修改`services.conf`

3. 服务器中，Web服务器安装
   - 支持nginx或caddy
   - 建议使用caddy（自动管理SSL证书）

4. 服务器中，反向代理配置
   ```bash
   # 方式一：自动配置（推荐）
   python3 server_load_proxy_from_config.py
   
   # 方式二：手动配置
   python3 server_create_nginx_proxy.py {配置参数}
   nginx -s reload  # 重载配置
   ```

5. 域名配置
   - 将域名解析到服务器公网IP

#### 访问优化（可选）
1. hosts优化
   - ZT网络设备：添加hosts指向NAS的ZT IP
   - 可绕过服务器直接访问NAS

2. 路由器优化
   - 配置域名劫持到NAS内网IP
   - 实现内外网智能切换
   > 注意：配置hosts后需要关闭才能使用内网直连

## 项目依赖

### NAS环境
- [acme.sh](https://github.com/acmesh-official/acme.sh)：SSL证书管理（Docker版）
- ZeroTier：虚拟局域网
- Docker：容器环境

### 服务器环境
- Nginx/Caddy：反向代理服务器
- ZeroTier：虚拟局域网
- Python 3.x：运行配置脚本

## 常见问题
1. 证书更新失败
   - 检查acme.sh容器状态
   - 确认域名解析是否正确
   - 验证更新脚本权限

2. 无法通过ZT访问
   - 检查ZT网络授权状态
   - 验证防火墙配置
   - 确认服务端口是否开放

3. 内外网切换问题
   - 确认路由器劫持配置
   - 检查hosts文件设置
   - 验证DNS解析是否正确

4. 方案二中访问失败
   - 检查服务器防火墙配置，可能为平台的防火墙，而非系统内软件

## 开源协议
本项目采用 MIT 开源协议。这意味着您可以：

- 自由使用：可以在任何场景下使用本项目
- 自由修改：可以修改源代码以适应您的需求
- 自由分发：可以分发原始或修改后的代码
- 商业使用：可以将本项目用于商业目的

### 使用限制
- 需要在副本中包含原始版权声明
- 作者不承担任何使用责任
- 不提供任何担保

详细条款请参阅 [MIT License](https://opensource.org/licenses/MIT)。


## TMDB 影视元数据同步脚本

新增脚本：`src/tmdb_media_sync.py`

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
- `--timeout`：单次网络请求超时秒数（默认 30）
- `--max-retries`：网络失败后的重试次数（默认 2）
- `--retry-backoff`：指数退避基础秒数（默认 2.0）
