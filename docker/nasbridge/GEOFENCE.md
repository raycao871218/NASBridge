# 地图选点与圆形围栏

“地理围栏”页可搜索地名、点击地图、拖动中心标记、使用浏览器定位或直接填写经纬度来确定中心点，然后调整半径。地图实时显示圆形范围，点击“保存围栏”后生效。支持单个圆形围栏；检测规则仍由手机执行，HA 仍由手机上报。

地图使用本地打包的 Leaflet 1.9.4 和 OpenStreetMap 在线底图，保留 OpenStreetMap 署名，无需填写地图 Key。地点搜索使用 Nominatim；仅主动提交时查询，单进程最多每秒一次，同一查询缓存一天（最多 100 项）。不提供自动补全、批量下载或地图瓦片代理。搜索失败仍可地图点选或输入坐标。在线地图、搜索均依赖网络；浏览器定位需要安全上下文（HTTPS 或 localhost）及用户授权，失败时不会改变已选中心点。

## 中心与半径

圆形围栏坐标统一为 **WGS-84**，半径单位为米，允许 100 米～500 公里。手机使用 GPS WGS-84 坐标计算与圆心的大圆距离即可，无需原来的县界多边形或 GCJ-02 转换。选点后的中心不会随地图平移或缩放而变化；拖动中心标记会明确修改它。

新增配置初始关闭，未选中心，不会擅自把地图默认视野当成围栏中心。旧版县域配置读取时保留原版本号、检测周期、误差阈值和测试模式，但暂停启用、清空中心，并提示重新选点。只有保存新的圆形围栏后才完成迁移，不会直接把县域改成一个任意圆圈。

## 时间和判断

正常模式默认每小时一次、最长 4 小时，外网后约第 1、2、3、4 小时检测。测试模式临时变成每 15 秒一次、最长 1 分钟，不覆盖正常周期。

- 内网：立即在家并取消检测。
- 距圆心距离大于 `radius_meters + boundary_buffer_meters + 定位精度半径`：确认离开，判定离家并结束检测。
- 仍在圆内、边缘不确定或定位失败：保留 HA 状态，继续下一次检测。
- 检测窗口结束，仍使用外网：按超时规则离家。
- 断网：未知，不算连续外网；短暂路由探测保留计时且不写入 HA。

客户端还需丢弃过旧、未来时间、无有效精度或精度超限的位置。Android 锁屏节电可能延后执行，使用经过时间而非回调次数判断窗口到期。

## 接口（schema_version = 2）

`GET /api/geofence/config` 返回 `settings`、实际生效的 `effective`、`fence` 和 `rules`。`PUT /api/geofence/config` 保存完整设置和 `expected_revision`。使用既有 PostgreSQL 状态表和 YAML 备份；并发旧版本保存返回 409。修改失败不得覆盖手机缓存的上次有效配置。沿用 NASBridge 现有访问控制，额外拒绝跨站浏览器写入。

```json
{
  "expected_revision": 0,
  "enabled": true,
  "name": "我的生活圈",
  "fence_type": "circle",
  "center_latitude": 31.62,
  "center_longitude": 104.46,
  "radius_meters": 10000,
  "check_interval_seconds": 3600,
  "max_detection_seconds": 14400,
  "boundary_buffer_meters": 1000,
  "max_location_age_seconds": 900,
  "max_location_accuracy_meters": 3000,
  "test_mode": false
}
```

`fence` 示例：`{"type":"circle","name":"我的生活圈","coordinate_system":"WGS-84","center":{"latitude":31.62,"longitude":104.46},"radius_meters":10000}`。没有中心点时 `center` 为 null 且不可启用。

`GET /api/geofence/search?q=北川永昌镇` 返回最多五项 `{name, latitude, longitude}`。固定请求 Nominatim，不允许客户端选择上游 URL。优先使用 `NASBRIDGE_GEOCODING_PROXY` 指定的服务端代理，未设置时使用已有 Mihomo 的 HTTP/Mixed 端口，无 Mihomo 配置则直连。代理凭据不返回浏览器。NAS 无法直接访问搜索服务时可在部署 `.env` 设置该变量，并在 Compose 的 nasbridge environment 中传入。

此版本移除了旧的 `/boundary`、`/preview` 县域接口。手机必须识别 schema 2，使用圆心和半径，不能继续按旧县界解释。当前 Android 版本尚未接入围栏，Web 保存不会立即改变 HA。

## 验证与署名

`python -m unittest discover -s tests -p 'test_geofence.py' -v` 覆盖坐标/半径、窗口、版本冲突、旧配置迁移、搜索限速缓存和错误处理；浏览器验证实际地图瓦片、选点、标记拖动、半径变化、保存刷新恢复、搜索选点、定位及手机宽度。

- [Leaflet](https://leafletjs.com/)：BSD-2-Clause，本地副本位于 `app/static/vendor/leaflet/`，许可证随包保留。
- [OpenStreetMap](https://www.openstreetmap.org/copyright) 与[地图瓦片使用政策](https://operations.osmfoundation.org/policies/tiles/)。
- [Nominatim 使用政策](https://operations.osmfoundation.org/policies/nominatim/)。
