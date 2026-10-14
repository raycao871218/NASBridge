# 阿里云安全组

NASBridge 顶部「阿里云安全组」页支持读取地域、VPC 安全组及全部入方向规则，预览并批量添加 TCP/UDP 放行规则。支持单端口、连续范围及 IPv4/IPv6 来源。每批最多 100 条，重复项去重；API 不会重复添加已存在的相同规则。

凭据来自容器环境变量 `ALIBABA_CLOUD_ACCESS_KEY_ID` 和 `ALIBABA_CLOUD_ACCESS_KEY_SECRET`。在此目录 `.env` 中配置，并通过 compose.yaml 的 environment 映射传入。更换凭据后执行 `docker compose up -d --force-recreate nasbridge`。不要把 `.env` 提交到版本库。

RAM 权限需包含：

```json
{"Version":"1","Statement":[{"Effect":"Allow","Action":["ecs:DescribeRegions","ecs:DescribeSecurityGroups","ecs:DescribeSecurityGroupAttribute","ecs:AuthorizeSecurityGroup"],"Resource":"*"}]}
```

流程：检查连接并加载地域 → 填写 ECS 所在地域 → 读取安全组 → 选择安全组 → 读取规则 → 填写端口、协议、来源 → 预览规则 → 确认开放端口。

来源 `0.0.0.0/0` 表示所有 IPv4，`::/0` 表示所有 IPv6。安全组规则影响关联实例。写入失败或超时时先刷新云端规则确认结果；成功提交不代表端口一定可连接，还取决于拒绝规则、主机防火墙和服务监听。此模块不删除规则，也不自动跟随新增反代开放端口。

运行测试（镜像包含 app，但 tests 不包含在生产镜像中）：将 tests/test_aliyun.py 放到容器 /tmp 后，使用 `PYTHONPATH=/app python /tmp/test_aliyun.py`。测试模拟 API，不修改云端。

参考：
- https://help.aliyun.com/zh/ecs/developer-reference/api-ecs-2014-05-26-authorizesecuritygroup
- https://help.aliyun.com/zh/ecs/developer-reference/api-ecs-2014-05-26-describesecuritygroupattribute
