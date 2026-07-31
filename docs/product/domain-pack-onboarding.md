# Atlas 动态领域包接入手册（v2）

本手册说明如何在不改动前端代码的前提下，新增一个领域并让 Atlas 自动暴露到搜索、分类、条目和关系视图。默认目标是“百科扩展优先”，硬件扫描/兼容能力保持可选，不作为主线入口依赖。

## 目标与边界

- **只改数据层、不过度改内核**：新增 `domain-pack` 只新增知识空间、分类、实体模型、字段、关系视图和质量档案；不会引入新页面路由或新的前端分支逻辑。
- **运行时加载**：API 在启动时读取 `HARDATLAS_DOMAIN_PACK_PATHS`，聚合并校验各包；发布后公开端通过动态接口读取 `spaces` / `taxonomy` / `entity types` / `view definitions`。
- **可回滚与审计**：每个 Domain Pack 变更都走草稿 → 提案 → 双人审核 → 发布/回滚流程。未通过审查不会直接生效。
- **安全前提**：生产环境不允许 fixture 后门功能，`seed_fixture_content=false` / `enable_hardware_fixture_extension=false`。

## 1）新建目录与文件

建议新包放在 `domain-packs/<pack-id>/`，例如 `domain-packs/geography/`。

最小目录示例：

```text
domain-packs/<pack-id>/
├── manifest.json
├── spaces/
├── taxonomy/
├── entity-types/
├── attributes/
├── relationship-types/
├── views/
└── quality/
```

`manifest.json` 为入口，字段最小集见下（其余字段可留空数组）：

```json
{
  "id": "astronomy",
  "name": "天文与空间百科",
  "version": "1.0.0",
  "kernelVersion": "2.0",
  "locales": ["zh-CN", "en"],
  "entityTypeFiles": ["entity-types/celestial-body.json"],
  "attributeFiles": ["attributes/discovery-year.json"],
  "relationshipTypeFiles": ["relationship-types/discovered-by.json"],
  "viewFiles": ["views/celestial-body-detail.json"]
}
```

字段说明（来自域模型运行期校验）：

- `id`：包 ID，必须全局唯一（不同包不允许重复）。
- `version`：包版本，发布和回滚都基于版本追踪。
- `kernelVersion`：内核版本（当前项目为 `2.0`）。
- `spaceFiles`/`entityTypeFiles`/`attributeFiles`/`relationshipTypeFiles`/`viewFiles`/`qualityProfileFiles`：
  所有条目均是该包内相对路径；除 `entityTypeFiles`/`attributeFiles`/`viewFiles` 外可按需为空。

## 2）编写关键模型文档

1. **知识空间（如需新增）**：`spaces/*.json`
   - 需要唯一 `id`/`slug`，并带 `rootTaxonomyNodeIds`。
2. **分类节点**：`taxonomy/*.json`
   - `spaceId` 必须指向存在的知识空间；
   - 根节点 `parentIds` 为空，且与 space 的 `rootTaxonomyNodeIds` 对齐；
   - 不允许跨空间引用与环。
3. **实体类型**：`entity-types/*.json`
   - `allowedTaxonomyNodeIds` 必须只引用本空间有效分类；
   - `attributeDefinitionIds`、`allowedRelationshipTypeIds` 必须可解析；
   - `defaultViewDefinitionId` 必须存在于 `viewFiles`。
4. **字段定义**：`attributes/*.json`
   - 新字段应声明类型、基数、是否必填、单位、可否多值等；
   - 与实体类型引用一致。
5. **关系定义**：`relationship-types/*.json`
   - 建议通过现有类型或跨包类型，避免重复造新枚举；
   - 若引用外部类型，必须确保目标在全局 registry 可见。
6. **视图定义**：`views/*.json`
   - 仅允许内核白名单的页面块（如 `hero`、`summary`、`attribute-table`）；
   - 组件化定义避免自由渲染。
7. **质量档案（推荐）**：`quality/*.json`
   - 至少为每个新增实体类型提供一个 `QualityProfile`，用于缺失值与一致性治理。

## 3）接入方式（两种）

- **方案 A：文件落盘加载（开发/快速验证）**
  1. 把新包目录放入仓库 `domain-packs/`。
  2. 在 `HARDATLAS_DOMAIN_PACK_PATHS` 中追加路径（见下一节）。
  3. 重启 API；如果校验通过，应用启动即加载。

- **方案 B：Admin 面板构造器（建议生产审批路径）**
  1. 打开 `/schema/domain-packs`，构造并保存草稿；
  2. 提交评审链路，完成双人审核；
  3. 通过后发布，系统写出 outbox 事件并进行索引 alias 切换。

## 4）配置 `HARDATLAS_DOMAIN_PACK_PATHS`

`apps/api/src/hardatlas_api/config.py` 与 `.env.example` 都支持逗号分隔列表。

示例：

```bash
HARDATLAS_DOMAIN_PACK_PATHS=domain-packs/core,domain-packs/animals,domain-packs/plants,domain-packs/electronics,domain-packs/my-domain
```

注意：

- 路径支持相对路径，相对项目根目录解析；
- 空项会被忽略，至少要有一个有效 pack；
- 生产环境可只保留已审核的稳定域包，实验域包建议放在开发环境配置里验证后再推广。

## 5）接入验收清单（建议执行）

1. **启动校验**
   - API 启动不报错，且 `GET /api/v1/domain-packs` 包含新 ID。
   - `GET /api/v1/spaces` 能看到新增 `space`；
2. **可发现性**
   - `/categories` 中可按新空间筛选；
   - `/search?q=<新增概念>` 能命中对应条目；
3. **治理路径**
   - 如有新类型，先通过草稿与高风险提案；
   - 只读对账：发布后 `/api/v1/domain-packs/{id}/{version}` 状态为 `published`。
4. **回滚安全（可选）**
   - `GET /api/v1/domain-packs/{id}/{version}/rollback-analysis` 返回 `safe` 后才允许回滚；
   - 回滚执行后通过 schema registry / taxonomy 验证关联清理是否完成。

## 常见阻断与排查

- `missing taxonomy`：分类节点引用了不存在或跨空间的 `spaceId`。
- `invalid parent`：存在父节点跨空间或闭环。
- `unresolved references`：关系/属性 ID 不在全局 registry 中，或类型缺少默认 view。
- `multiple quality profiles`：同一实体类型配置了重复质量档案。

出现阻断时先在本地先运行构造器/测试环境的 `validate` 流程，再提交。
