# ZeroTier 配置指南

## 问题描述

宿主机运行 ZeroTier 服务并作为控制器，同时运行 ZTNCUI Web 管理面板容器时出现以下问题：

- 宿主机 ZeroTier 客户端卡在 `REQUESTING_CONFIGURATION`
- ZTNCUI 报错 `401 Unauthorized`
- 端口 9993 冲突

## 解决方案

### 1. 宿主机配置

```bash
# 允许本地 API 访问
echo '{"settings": {"allowManagementFrom": ["127.0.0.1"]}}' > /var/lib/zerotier-one/local.conf
systemctl restart zerotier-one

# 检查服务状态
zerotier-cli status
```

### 2. 启动 ZTNCUI 容器

```bash
docker run -d --name ztncui \
  --network host \
  -v /var/lib/zerotier-one:/var/lib/zerotier-one \
  -e HTTP_PORT=14000 \
  -e HTTP_ALL_INTERFACES=yes \
  -e ZTNCUI_PASSWD=mrdoc.fun \
  --restart=on-failure:3 \
  keynetworks/ztncui
```

### 3. 验证配置

```bash
# 检查容器状态
docker ps | grep ztncui

# 检查端口占用
ss -ulnp | grep 9993

# 查看容器日志
docker logs ztncui
```

## 关键配置说明

### `--network host`
- 让容器直接使用宿主机网络栈，避免端口冲突
- 无需单独映射 9993 端口

### `-v /var/lib/zerotier-one:/var/lib/zerotier-one`
- 共享宿主机认证文件和网络配置
- 解决 401 未授权问题

### `allowManagementFrom` 设置
- 确保 ZeroTier 允许本地 API 访问
- 允许 ZTNCUI 容器通过本地 API 管理 ZeroTier