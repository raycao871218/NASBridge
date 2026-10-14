# ZeroTier 控制器集成

面板「ZeroTier」页使用现有反代管理的 SSH 连接，在控制器服务器读取 ztncui 容器的 authtoken.secret，并调用 127.0.0.1:9993 的控制器 API。Token 不传回 NASBridge 或浏览器；没有新开放的管理端口。当前部署适用于 ztncui 的 host 网络模式。

支持：网络列表、成员列表、地址池与路由查看、成员备注/IP/授权修改。授权状态不等于在线状态。不包含网络删除或路由编辑。原 ztncui 仍可使用。

修改成员时仅提交变化字段，用户确认后写入并回读验证。取消授权或修改 IP 会影响设备连通性，尤其不要误改承载面板到服务器 SSH 连接的成员。

代码：app/zerotier.py、app/static/zerotier.js；测试 tests/test_zerotier.py。联调仅查询现有网络，写入通过模拟测试验证，不修改生产成员。

备注从 ztncui etc/storage（node-persist）读取。修改备注会先备份，再原子写回并仅重启 ztncui 网页进程以刷新其内存缓存；不重启 ZeroTier 网络服务。备注按成员 ID 跨网络共享，与原 ztncui 一致。NASBridge 旧本地备注不再覆盖原服务。授权和 IP 修改继续走控制器 API。

“打开原管理界面”读取本地 `config/config.yaml` 的 `zerotier.admin_url`，仅接受不含 URL 账号密码的 HTTP/HTTPS 地址。未配置时提示缺少配置，不再内置个人管理域名。
