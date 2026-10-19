# 鸟巢 · NASBridge Docker 管理面板

这是 NAS 上实际运行的 NASBridge 网页与接口服务源码。管理面板名为「鸟巢」，Android 客户端名为「麻雀会」，采用与「咕咕咕」一致的暖纸蜡笔风，见 [BRANDING.md](BRANDING.md)。当前版本包括：

- Mihomo 在线状态、连接和流量统计、配置编辑，以及指定容器的启停控制。
- 本地和远端证书检查、证书同步计划、远端 Nginx 反向代理管理。
- 阿里云安全组、ZeroTier 网络与成员管理。
- 每日任务记录、文章全文、已读状态、犀牛哥视频列表、缩略图与 HTTP Range 播放接口。
- 短信归档：分页、搜索、展开全文和验证码复制。
- 视频 Tab：封面列表、标题搜索、在线播放、倍速、前后跳转与浏览器原生全屏；切换 Tab 自动暂停。列表展示接口返回的最近最多 200 部视频，播放编码支持取决于浏览器。
- 每日任务手动执行：今天没有成功记录时提供入口，运行中禁止重复启动；连接设置见 [执行端配置](ops/DAILY_TASK_CONTROL.md)。
- 每日任务阶段进度：实时显示当前及已完成阶段、最近心跳与进展；超时/中断记录会自动纠正，执行端部署及判定规则见 [进度记录](ops/runner/README.md)。
- 地理围栏：真实地图搜索选点、拖动圆心、半径调整、周期与时长配置，以及供手机读取的圆形围栏接口（手机执行与 HA 上报后续接入）。

Android App 单独维护，通过这些 HTTP API 访问本服务；本目录不包含 Android 工程，也不包含推送服务。

## 首次部署

在仓库根目录执行以下步骤。已经运行的 NAS 不需要覆盖配置或重新初始化：

```sh
cd docker/nasbridge
cp compose.example.yaml compose.yaml
cp .env.example .env
cp config/config.yaml.example config/config.yaml
mkdir -p data
```

编辑 `.env` 中的数据库账号、可选云服务凭据和宿主机挂载路径；编辑 `config/config.yaml` 中的 Mihomo、应用、证书和远端 SSH 连接。`NASBRIDGE_DOMAINS_CONFIG` 指向项目的真实域名配置，可参考仓库根目录 `domains_config.yaml.example`。挂载的配置文件应预先存在。

```sh
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml up -d --build
```

浏览器访问 `http://<NAS-IP>:18080`。网络示例使用 `172.20.0.0/16`，如与已有 Docker 网络冲突请调整。Docker 构建期间的 pip 代理可在 `.env` 中设置 `NASBRIDGE_BUILD_HTTP_PROXY`、`NASBRIDGE_BUILD_HTTPS_PROXY` 和 `NASBRIDGE_BUILD_ALL_PROXY`；Docker daemon 拉取基础镜像的代理需另外配置。

视频以只读方式挂载到 `/app/media/rhino`，由现有索引关联文件；FFmpeg 由固定版本 `imageio-ffmpeg` 包提供。应用配置通过宿主机挂载读取，真实配置不会复制到新镜像中。

## 日常开发与更新部署

推荐流程：**本地修改和验证 → 提交并推送 GitHub → NAS 拉取 → 构建、测试和更新容器**。日常源码修改统一在本地完成。

### 分支约定

源码导入 PR #23 已合并，NAS 后续从 `master` 部署，本地通过功能分支开发并提交 PR。先执行 `git branch --show-current` 确认当前分支；推送功能分支不会自动更新 `master`。

如果本地或 NAS 仍停留在原源码导入分支，请在工作区干净时执行一次：

```sh
git fetch origin
git checkout master
git pull --ff-only origin master
```

以后通过功能分支开发、PR 合并到 `master`，NAS 从 `master` 部署。切换前先确认工作区干净。

### 本地提交

进入本地 NASBridge 仓库根目录，先更新分支，再修改和测试代码：

```sh
git status --short --branch
git pull --ff-only
# 修改代码，并按下方“验证”章节运行相应测试。
git add .gitignore README.md docker/nasbridge
git diff --cached --stat
git diff --cached --check
git commit -m "描述本次修改"
git push
```

提交前查看暂存差异，确认不包含真实域名、内网地址、密码或运行数据。新增其他目录的文件时按需单独暂存，不使用 `git add -f` 绕过忽略规则。使用 PR 时，部署前应先确认改动已合并到 NAS 使用的分支。

### NAS 拉取与部署

通过 SSH 登录 NAS，进入该仓库根目录。以下示例使用普通 `git`、`docker`；群晖若提示找不到命令，分别使用 `/usr/local/bin/git`、`/usr/local/bin/docker`，Docker 权限不足时添加 `sudo`。

先检查 `git status --short --branch`。工作区干净、分支正确后，顺序执行下列命令；`&&` 确保拉取、构建或测试失败时不会继续部署：

```sh
git pull --ff-only && \
cd docker/nasbridge && \
docker compose -f compose.yaml config --quiet && \
docker compose -f compose.yaml build && \
docker run --rm --network none \
  -e NASBRIDGE_CONFIG=/app/config/config.yaml.example \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/ops:/app/ops:ro" \
  nasbridge:local python -m unittest discover -s tests -p 'test_*.py' -v && \
docker compose -f compose.yaml up -d
```

部署后检查容器状态和最近日志，并在网页确认功能：

```sh
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs --tail=100 nasbridge
```

已有 NAS 不需要再次复制首次部署的模板。`.env`、`compose.yaml`、真实应用配置与运行数据均被忽略，正常拉取不会覆盖；如果新版本调整了模板，需对照变更手动更新本地配置。

### 拉取冲突与失败

如果 `git pull --ff-only` 提示本地修改或分支分叉，停止部署，先用 `git status` 和 `git diff` 检查。备份并将需要保留的 NAS 修改整理为提交，再合并处理；不要通过 `git reset --hard` 或 `git clean -fd` 直接丢弃现场内容。构建或测试失败时先修复问题，原有运行容器仍继续使用旧镜像。

## 数据与接口

数据库连接通过 `NASBRIDGE_POSTGRES_HOST/PORT/DATABASE/USER/PASSWORD` 环境变量配置。

- `public.nasbridge_state`：面板状态，应用负责初始化；旧 YAML 状态可迁移，运行时文件保存在 `data/`。
- `public.rhino_daily_runs`、`public.rhino_video_summaries`：由现有每日任务系统维护，面板读取任务与文章。
- `message_redirect.sms_messages`：由短信转存系统维护，面板只读。数据库用户需要该 schema 的 USAGE 和该表的 SELECT 权限；本服务不负责创建或写入短信表。

移动端接口见 `app/mobile_api.py` 和 `app/sms_api.py`。短信每页默认 40 条，发送人/内容搜索使用绑定参数，响应禁止缓存；验证码仅在明确识别到一个 4～8 位数字代码时提供复制按钮。手机接口采用现有 Host 路由，无需预先手动检测。

## 验证

在已构建镜像的目录执行（使用示例配置，无需读取生产凭据或连接生产网络）：

```sh
docker run --rm --network none \
  -e NASBRIDGE_CONFIG=/app/config/config.yaml.example \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/ops:/app/ops:ro" \
  nasbridge:local python -m unittest discover -s tests -p 'test_*.py' -v
```

测试通过 mock 或临时目录验证 API，不应写入真实短信、文章已读状态或云资源。阿里云前端逻辑测试仅使用 Node.js 内置模块，可执行 `node test_aliyun_ui.cjs`。
视频前端测试同样无需依赖或生产连接：`node test_videos_ui.cjs`。
每日任务前端测试：`node test_daily_task_ui.cjs`。

## 配置与访问边界

`.env`、真实 `compose.yaml`、`config/config.yaml`、`data/`、`backups/` 和历史 `*.before-*`/`*.bak` 文件保留在部署机器，均不提交。代码仓库只提供部署模板；请单独备份真实配置和数据库。

管理 API 沿用现有部署的访问控制，本身没有统一登录认证。短信包含个人内容，应通过可信内网或带认证的反向代理访问。NASBridge 主容器不挂载 Docker socket；独立的 `mihomo-control` 容器通过内部网络提供固定 Mihomo 容器的状态和启停操作，并挂载宿主机 Docker socket，因此仍需限制其容器访问权限。证书和 ZeroTier 管理沿用配置中的固定 SSH 连接。

其他模块说明见 [ALIYUN.md](ALIYUN.md)、[ZEROTIER.md](ZEROTIER.md) 与 [GEOFENCE.md](GEOFENCE.md)。

ZeroTier 的“打开原管理界面”从本地 `config/config.yaml` 的 `zerotier.admin_url` 读取；仓库不保存实际管理域名。

## App 扫码配置

后台“App 扫码配置”页可将家庭 Wi-Fi、内外网 NAS 地址与端口、HA 地址、实体、长期访问令牌、手机测试模式和当前围栏合为一个二维码。使用 NASBridge Android 0.16.0+，在 App “配置 → 扫码导入配置”预览确认后保存。HA 配置和围栏均可选择不包含。导入后自动同步停止，可在手机启用；系统权限和电池限制仍需手机授权。

二维码内携带配置，请只给自己的手机扫码，不要分享包含令牌的下载文件。点击“保存配置”后，配置和 HA 令牌加密保存到 NAS 持久化数据目录；刷新或重启后自动加载，令牌输入框留空会保留已保存的令牌。读取接口只返回是否保存令牌，不返回明文。页面二维码 5 分钟后隐藏，后台已保存配置保留，下载文件仍可导入。生成接口沿用面板既有访问控制，应在可信内网或受保护的反向代理后使用。不会把数据库、SMTP、云服务等仅由服务端使用的凭据放入手机。

`POST /api/android-setup/qr.svg` 请求包含 `network: {ssid, local_url, public_url}`、可选的 `presence: {url, entity, token, test_mode}` 和 `include_geofence`（默认 true）。二维码文本使用 `NASBRIDGE1:` 加 zlib 压缩 JSON 的无填充 Base64URL，解压后的协议为 `type=nasbridge.android.config`、`version=1`。压缩用于提高扫码成功率，不是加密；手机限制解压数据最多 8 KiB。只接受同源 JSON POST，请求最多 8 KiB、二维码内容最多 2000 字节，格式错误不会回显令牌。服务器不会根据提交的地址发起网络请求。地址可省略协议：内网 NAS 默认补 http://，外网 NAS 和 HA 默认补 https://；显式填写的协议保留。保存与二维码均使用补全后的地址。HA 地址须为 HTTPS，服务地址不可包含账号、查询参数或片段。

保存接口：`GET /api/android-setup/config` 读取已保存配置（不返回令牌）；`PUT /api/android-setup/config` 保存完整配置及 `expected_revision`，旧版本写入返回 409。首次保存 HA 配置必须填写令牌，之后保存或生成二维码时令牌留空可复用已保存值。取消包含 HA 后保存会移除保存的 HA 配置。配置使用 Fernet 加密，原子替换写入，文件与密钥权限为 0600；保存在数据卷的 `android-setup.enc`、`android-setup.key`，备份和恢复时需同时保留两者。接口沿用 NAS 面板已有访问控制；有访问权限的面板使用者可生成包含已保存令牌的二维码。

家庭 Wi-Fi 名称可留空（或省略 `network.ssid`），由 Android 0.16.1+ 在扫码时申请精确定位权限、读取手机当前已连接的 Wi-Fi，并在导入预览中确认。读取失败可重新读取或手动填写；确认前不会保存。后台不推测手机 SSID，也不读取 Wi-Fi 密码。已填写 SSID 的二维码仍按填写值导入，兼容旧版 App。
