# Schema 影响分析与迁移门

## 已实现的分析层

`POST /api/v1/schema-changes/analyze` 接收 Schema 类型、当前文档 ID 和候选版本 JSON。
服务端从不可变注册表读取当前版本，不信任客户端传入的 before 文档，并执行：

- 文档 ID 不可改变、候选版本必须提升；
- Attribute 的键、数据类型、基数、必填、枚举、单位和校验差异分类；
- EntityType 的字段集合、分类边界、关系白名单、知识空间和默认视图差异分类；
- RelationshipType 的有向性、两端类型、基数、限定字段和证据要求差异分类；
- ViewDefinition 的安全页面块变化按显示兼容变更处理；
- 扫描当前实体修订，计算受影响实体和关系数量。

输出采用 `additive`、`compatible`、`migratory`、`breaking` 四级。Breaking 变更默认不可
自动回滚，并阻断后续执行，直到提供显式转换器和回滚黄金夹具。

Admin `/schema/migrations` 提供候选 JSON 编辑和只读影响报告。分析不会注册 Schema、
创建提案或修改实体。

## 迁移执行层

迁移计划会创建高风险 `schema` 提案，并冻结受影响实体及其输入修订 ID。策略门要求两名
不同审核者批准；未达到 `accepted` 时执行接口返回冲突，不会写入候选 Schema。

执行器只接受白名单声明式操作：

- `set-default`、`remove-claim`、`rename-claim`；
- `map-enum`、`cast-claim`；
- `remove-relationship`、`remap-taxonomy`。

不加载任意 Python、JavaScript、Prompt 或网络工具。转换后实体必须重新通过当前候选
EntityType、AttributeDefinition 和 RelationshipType 校验。任一实体失败会回滚整个事务。

数据库用 `schema_activation` 明确记录每个 Schema 文档的当前激活版本，避免以创建时间
猜测“最新”。执行在一个事务中：

1. 核对激活版本仍等于 `fromVersion`；
2. 核对所有当前实体修订仍等于冻结输入；
3. 写入不可变候选 Schema 并切换激活指针；
4. 写入全部新实体修订并切换当前修订；
5. 重建关系当前投影并写入 Outbox；
6. 将迁移清单标记为 `applied`。

回滚要求所有迁移后实体仍指向该清单生成的修订；随后同一事务恢复 `fromVersion` 激活
指针、冻结的实体修订与关系投影。应用或回滚之后发生的新编辑会阻断回放，防止覆盖新数据。

Admin `/schema/migrations` 可创建清单、跳转治理提案、执行与回滚。页面会真实显示未审核
执行的拒绝结果。
