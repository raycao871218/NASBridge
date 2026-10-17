# 地理围栏配置

Web 导航中的“地理围栏”管理北川羌族自治县的县域边界、检测间隔和最长检测时间。此模块仅提供配置；不采集位置、不接收轨迹、不保存 HA 凭据，也不调用 HA。当前 Android 0.14.3 尚未接入围栏配置，手机仍执行既有内外网规则。

## 行为

初始关闭，正常模式默认每小时一次、最长 4 小时，共 4 次。切至外网后第一次检测在一个周期后进行（正常模式约第 1、2、3、4 小时）。恢复内网立即判定在家并取消本轮定位；确认出县提前判定离家，结束本轮。位置仍在县内或无法定位时保留 HA 状态并继续下一次；窗口结束后仍处于外网则按超时规则离家。在县内不等于在家，只有内网结果能立即判定在家。

测试模式固定每 15 秒一次、最长 1 分钟，共 4 次，正式周期保留不变。关闭围栏表示不启动位置检测，既有内外网规则仍由手机执行。

手机断网时不把断网算成外网，取消当前窗口并保留 HA 状态；短暂路由 ping 探测则保留窗口，探测期间不写 HA。Android 的锁屏节电可能延后任务，不能以任务准点执行为前提。时间规则必须用经过时间计算，不能仅以回调次数计时。

## 数据和保存

复用 `public.nasbridge_state`，状态键为 `geofence_config`，并沿用已有本地 YAML 备份机制，路径为 `data/geofence.yaml`。配置修改时写 PostgreSQL，成功后更新备份；数据库写入失败返回 503，不把内存更改当成保存成功。配置锁与现有单进程 Uvicorn 部署一致；扩展为多 worker 前需将版本检查与更新改成数据库原子条件写入。

首次读取返回关闭状态。保存携带 `expected_revision`，成功时版本 +1；旧版本保存返回 409，前端要求重新读取，避免相互覆盖。

## 手机读取接口（schema_version = 1）

- `GET /api/geofence/config`：读取配置、有效时间规则、边界摘要及执行约定。
- `PUT /api/geofence/config`：Web 保存完整配置与 `expected_revision`。拒绝跨站浏览器写入，沿用 NASBridge 既有部署的访问控制。
- `GET /api/geofence/boundary`：县界 GeoJSON Feature、坐标系和 SHA-256。
- `GET /api/geofence/preview`：Web 预览使用的绵阳市县级边界集合。

配置示例（PUT 请求）：

```json
{
  "expected_revision": 0,
  "enabled": false,
  "region_id": "510726",
  "check_interval_seconds": 3600,
  "max_detection_seconds": 14400,
  "boundary_buffer_meters": 1000,
  "max_location_age_seconds": 900,
  "max_location_accuracy_meters": 3000,
  "test_mode": false
}
```

`settings` 是保存的规则；`effective` 是应用测试模式后的实际间隔、窗口和最大次数。读取时使用 `effective`，不要在手机写死测试时长。`revision` 和 `updated_at` 标识配置更新；`region.boundary_sha256` 标识边界版本。所有接口禁止缓存，手机可显式持久化最后一次校验通过的配置，断网时不使用空响应覆盖它。

`boundary` 响应的 `sha256` 对应包内原始 `beichuan.geojson` 文件的 UTF-8 字节摘要（包含末尾换行），不是 JSON 重新序列化后的摘要。用于识别已下载的边界版本；与配置中的 `boundary_sha256` 比较。客户端初次下载必须验证响应结构、行政区代码与坐标系。

## 坐标和误判边界

县界来源为 [DataV 绵阳行政区划](https://geo.datav.aliyun.com/areas_v3/bound/510700_full.json)，获取日期 2026-10-16，完整保留发布的几何数据，不是以县城为中心画圆。来源与日期在 GeoJSON 属性中记录。该数据用于近似范围判断，不作为测绘或行政边界确权依据。

边界坐标系为 **GCJ-02**，与原生 GPS 的 WGS-84 不同。[DataV 坐标系说明](https://help.aliyun.com/zh/datav/datav-7-0/user-guide/map-data-format-1)。手机必须先将位置转换为同一坐标系，再进行 Polygon/MultiPolygon（含内环）判断，不得直接混用经纬度。

手机丢弃超出 `max_location_age_seconds` 的旧位置、超过 `max_location_accuracy_meters` 的低精度位置以及未来时间、无有效精度的结果，视为定位失败。定位点在县界外，且到县界的距离大于 `boundary_buffer_meters + 定位精度半径`，才认定已出县；落在缓冲范围内视为不确定，保留状态并继续后续检测。

## 验证

```sh
python -m unittest discover -s tests -p 'test_geofence.py' -v
```

覆盖关闭默认值、时间窗口、测试模式、并发版本冲突、参数边界、坐标及边界版本一致性、错误清理、跨站写入与持久化适配。浏览器验证应包括桌面与手机宽度、保存回读、页面刷新恢复、冲突时保留表单和接口失败重试。
