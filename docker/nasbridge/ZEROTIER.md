# ZeroTier 控制器集成

面板「ZeroTier」页使用现有反代管理的 SSH 连接，在控制器服务器读取 ztncui 容器的 authtoken.secret，并调用 127.0.0.1:9993 的控制器 API。Token 不传回 NASBridge 或浏览器；没有新开放的管理端口。当前部署适用于 ztncui 的 host 网络模式。

支持：网络列表、成员列表、地址池与路由查看、成员备注/IP/授权修改。授权状态不等于在线状态。不包含网络删除或路由编辑。原 ztncui 仍可使用。

修改成员时仅提交变化字段，用户确认后写入并回读验证。取消授权或修改 IP 会影响设备连通性，尤其不要误改承载面板到服务器 SSH 连接的成员。

代码：app/zerotier.py、app/static/zerotier.js；测试 tests/test_zerotier.py。联调仅查询现有网络，写入通过模拟测试验证，不修改生产成员。

备注从 ztncui etc/storage（node-persist）读取。修改备注会先备份，再原子写回并仅重启 ztncui 网页进程以刷新其内存缓存；不重启 ZeroTier 网络服务。备注按成员 ID 跨网络共享，与原 ztncui 一致。NASBridge 旧本地备注不再覆盖原服务。授权和 IP 修改继续走控制器 API。

## 自建 Planet 与现有控制器共存

Planet 负责根节点发现，controller 负责网络、成员授权和 IP 分配。现有 ZeroTier 节点可同时担任两种角色；增加 Planet 无需替换 ztncui 或重新创建网络。本适配器仍使用原容器名、存储路径和本地 API，因此无需更换 NASBridge 的适配代码。

迁移时保留 `/var/lib/zerotier-one` 中的节点身份、`controller.d`、`networks.d` 和认证文件，并备份 ztncui 的 `/opt/key-networks/ztncui/etc` 持久化数据。不要让全新容器的初始化脚本重建身份，否则原网络 ID 将无法沿用。

可使用与现有服务版本一致的 [ZeroTierOne mkworld 源码](https://github.com/zerotier/ZeroTierOne/tree/1.14.2/attic/world)，将根节点配置为现有节点的 **identity.public** 和可从公网访问的 UDP 端点，生成并签署 Planet。保留生成器的 `current.c25519` 与 `previous.c25519` 签名密钥，供日后签署更新；这些密钥、节点私钥、控制器备份和真实端点不得提交 Git。客户端只需要生成的 `planet` 文件。

切换前验证不依赖 ZeroTier 的 SSH 管理通道，并安排定时回滚。服务器和每个客户端分别备份、替换数据目录中的 `planet`，再重启对应 ZeroTier 服务。分批迁移期间可保留已有 Moon 文件；仅更换服务器文件不会自动迁移所有客户端。移动端必须先核实实际客户端是否支持导入自定义 Planet。

验收应包括客户端 `zerotier-cli peers` 中自建根节点显示为 `PLANET`、链路为 `DIRECT`，原网络仍为 `OK` 且 IP 不变，以及迁移前后网络配置、成员授权和 IP 分配逐项一致。还需验证 NASBridge 的网络列表和成员列表接口。通过后取消回滚计时器。回滚只恢复该节点原来的 `planet` 并重启服务，避免覆盖运行期间可能更新的成员数据。
