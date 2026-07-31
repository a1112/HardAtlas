from datetime import UTC, datetime

from hardatlas_domain import (
    AgentProposal,
    AgentRole,
    AgentRun,
    AttributeDataType,
    AttributeDefinition,
    ChangeOperation,
    Citation,
    ClaimValue,
    ContentSection,
    EntityRef,
    EntityType,
    KnowledgeEntity,
    KnowledgeSpace,
    LocalizedText,
    RelationshipType,
    RevisionContext,
    TaxonomyNode,
    ViewBlock,
    ViewDefinition,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def zh(value: str, *, generated: bool = False) -> LocalizedText:
    return LocalizedText(locale="zh-CN", value=value, machine_generated=generated)


def en(value: str, *, generated: bool = False) -> LocalizedText:
    return LocalizedText(locale="en", value=value, machine_generated=generated)


SPACES = [
    KnowledgeSpace(
        id="space-life",
        slug="life",
        name=[zh("生命科学")],
        description=[zh("动物、植物、微生物、生态与演化")],
        root_taxonomy_node_ids=["tax-animals", "tax-plants"],
        icon_key="life",
        status="published",
    ),
    KnowledgeSpace(
        id="space-engineering",
        slug="engineering",
        name=[zh("工程技术")],
        description=[zh("电子、材料、机械、计算与制造")],
        root_taxonomy_node_ids=["tax-electronics"],
        icon_key="engineering",
        status="published",
    ),
    KnowledgeSpace(
        id="space-earth",
        slug="earth",
        name=[zh("地球与环境")],
        description=[zh("地理、气候、地质与自然现象")],
        root_taxonomy_node_ids=["tax-geography", "tax-geology"],
        icon_key="earth",
        status="published",
    ),
    KnowledgeSpace(
        id="space-humanities",
        slug="humanities",
        name=[zh("人文与历史")],
        description=[zh("人物、事件、文化、语言与艺术")],
        root_taxonomy_node_ids=["tax-history", "tax-language"],
        icon_key="humanities",
        status="published",
    ),
    KnowledgeSpace(
        id="space-mathematics",
        slug="mathematics",
        name=[zh("数学与逻辑")],
        description=[zh("数学基础、模型、统计与计算推理")],
        root_taxonomy_node_ids=["tax-mathematics"],
        icon_key="mathematics",
        status="published",
    ),
    KnowledgeSpace(
        id="space-medicine",
        slug="medicine",
        name=[zh("医学与健康")],
        description=[zh("解剖、生理、疾病、治疗与公共健康")],
        root_taxonomy_node_ids=["tax-medicine"],
        icon_key="medicine",
        status="published",
    ),
    KnowledgeSpace(
        id="space-oceanography",
        slug="oceanography",
        name=[zh("海洋与水圈")],
        description=[zh("海洋地貌、深海地形与相关观测实体")],
        root_taxonomy_node_ids=["tax-marine-objects"],
        icon_key="earth",
        status="published",
    ),
]

TAXONOMY = [
    TaxonomyNode(
        id="tax-animals",
        space_id="space-life",
        slug="animals",
        name=[zh("动物")],
        parent_ids=[],
        child_count=14,
        entity_count=482_316,
        path_keys=["life", "animals"],
    ),
    TaxonomyNode(
        id="tax-plants",
        space_id="space-life",
        slug="plants",
        name=[zh("植物")],
        parent_ids=[],
        child_count=11,
        entity_count=391_084,
        path_keys=["life", "plants"],
    ),
    TaxonomyNode(
        id="tax-electronics",
        space_id="space-engineering",
        slug="electronic-components",
        name=[zh("电子元件")],
        parent_ids=[],
        child_count=18,
        entity_count=126_540,
        path_keys=["engineering", "electronic-components"],
    ),
    TaxonomyNode(
        id="tax-geography",
        space_id="space-earth",
        slug="geography",
        name=[zh("地理")],
        parent_ids=[],
        child_count=12,
        entity_count=120_100,
        path_keys=["earth", "geography"],
    ),
    TaxonomyNode(
        id="tax-geology",
        space_id="space-earth",
        slug="geology",
        name=[zh("地质")],
        parent_ids=[],
        child_count=7,
        entity_count=48_200,
        path_keys=["earth", "geology"],
    ),
    TaxonomyNode(
        id="tax-history",
        space_id="space-humanities",
        slug="history",
        name=[zh("历史")],
        parent_ids=[],
        child_count=9,
        entity_count=210_000,
        path_keys=["humanities", "history"],
    ),
    TaxonomyNode(
        id="tax-language",
        space_id="space-humanities",
        slug="language",
        name=[zh("语言与文字")],
        parent_ids=[],
        child_count=16,
        entity_count=75_300,
        path_keys=["humanities", "language"],
    ),
    TaxonomyNode(
        id="tax-mathematics",
        space_id="space-mathematics",
        slug="mathematics",
        name=[zh("数学")],
        parent_ids=[],
        child_count=14,
        entity_count=80_120,
        path_keys=["mathematics", "mathematics"],
    ),
    TaxonomyNode(
        id="tax-medicine",
        space_id="space-medicine",
        slug="medicine",
        name=[zh("医学")],
        parent_ids=[],
        child_count=17,
        entity_count=310_430,
        path_keys=["medicine", "medicine"],
    ),
    TaxonomyNode(
        id="tax-marine-objects",
        space_id="space-oceanography",
        slug="marine-objects",
        name=[zh("海洋对象")],
        parent_ids=[],
        child_count=2,
        entity_count=1,
        path_keys=["oceanography", "marine-objects"],
    ),
    TaxonomyNode(
        id="tax-deep-sea",
        space_id="space-oceanography",
        slug="deep-sea",
        name=[zh("深海地形")],
        parent_ids=["tax-marine-objects"],
        child_count=0,
        entity_count=1,
        path_keys=["oceanography", "marine-objects", "deep-sea"],
    ),
]

RELATIONSHIP_TYPES = [
    RelationshipType(
        id="rel-taxonomic-parent",
        key="taxonomic_parent",
        name=[zh("分类上级")],
        inverse_name=[zh("包含分类")],
        directed=True,
        source_entity_type_ids=["type-animal", "type-plant"],
        source_cardinality="one",
        qualifier_schema={"rank": {"type": "string"}},
        schema_version="relations-life-1.0.0",
    ),
    RelationshipType(
        id="rel-distributed-in",
        key="distributed_in",
        name=[zh("分布于")],
        inverse_name=[zh("物种分布")],
        directed=True,
        source_entity_type_ids=["type-animal", "type-plant"],
        qualifier_schema={
            "status": {
                "type": "string",
                "enum": ["native", "introduced", "historic"],
            }
        },
        schema_version="relations-life-1.0.0",
    ),
    RelationshipType(
        id="rel-variant-of",
        key="variant_of",
        name=[zh("变体属于")],
        inverse_name=[zh("具有变体")],
        directed=True,
        source_entity_type_ids=["type-electronic-component"],
        target_entity_type_ids=["type-electronic-component"],
        source_cardinality="one",
        qualifier_schema={"manufacturer": {"type": "string"}},
        schema_version="relations-electronics-1.0.0",
    ),
    RelationshipType(
        id="rel-used-in",
        key="used_in",
        name=[zh("用于")],
        inverse_name=[zh("使用元件")],
        directed=True,
        source_entity_type_ids=["type-electronic-component"],
        qualifier_schema={"function": {"type": "string"}},
        schema_version="relations-electronics-1.0.0",
    ),
    RelationshipType(
        id="rel-in-depth-of",
        key="in-depth-of",
        name=[zh("位于深海地形中")],
        inverse_name=[zh("覆盖海洋对象")],
        directed=True,
        source_entity_type_ids=["type-sea-feature"],
        target_entity_type_ids=["type-sea-feature"],
        source_cardinality="many",
        target_cardinality="many",
        qualifier_schema={"region": {"type": "string"}},
        evidence_required=False,
        schema_version="relations-oceanography-1.0.0",
    ),
]

ATTRIBUTES = [
    AttributeDefinition(
        id="attr-scientific-name",
        key="scientific_name",
        name=[zh("学名")],
        data_type=AttributeDataType.TEXT,
        cardinality="one",
        required=True,
        schema_version="schema-2.0.0",
    ),
    AttributeDefinition(
        id="attr-conservation",
        key="conservation_status",
        name=[zh("保护状况")],
        data_type=AttributeDataType.ENUM,
        cardinality="one",
        required=False,
        enum_values=["LC", "NT", "VU", "EN", "CR", "EW", "EX"],
        schema_version="schema-2.0.0",
    ),
    AttributeDefinition(
        id="attr-supply-voltage",
        key="supply_voltage",
        name=[zh("供电电压")],
        data_type=AttributeDataType.MEASUREMENT,
        cardinality="one",
        required=True,
        unit_family="voltage",
        schema_version="schema-2.0.0",
    ),
    AttributeDefinition(
        id="attr-sea-depth",
        key="max_depth_meters",
        name=[zh("最大深度（米）")],
        data_type=AttributeDataType.MEASUREMENT,
        cardinality="one",
        required=True,
        unit_family="distance",
        schema_version="schema-1.0.0",
    ),
]

ENTITY_TYPES = [
    EntityType(
        id="type-animal",
        space_id="space-life",
        key="animal",
        name=[zh("动物")],
        description=[zh("动物分类、形态、分布、生态与保护信息")],
        allowed_taxonomy_node_ids=["tax-animals"],
        attribute_definition_ids=["attr-scientific-name", "attr-conservation"],
        allowed_relationship_type_ids=["rel-taxonomic-parent", "rel-distributed-in"],
        default_view_definition_id="view-animal",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-plant",
        space_id="space-life",
        key="plant",
        name=[zh("植物")],
        description=[zh("植物分类、形态、分布、生态与用途")],
        allowed_taxonomy_node_ids=["tax-plants"],
        attribute_definition_ids=["attr-scientific-name"],
        allowed_relationship_type_ids=["rel-taxonomic-parent", "rel-distributed-in"],
        default_view_definition_id="view-plant",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-electronic-component",
        space_id="space-engineering",
        key="electronic-component",
        name=[zh("电子元件")],
        description=[zh("器件功能、引脚、参数、封装、变体与应用")],
        allowed_taxonomy_node_ids=["tax-electronics"],
        attribute_definition_ids=["attr-supply-voltage"],
        allowed_relationship_type_ids=["rel-variant-of", "rel-used-in"],
        default_view_definition_id="view-electronic-component",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-geography",
        space_id="space-earth",
        key="geography",
        name=[zh("地理地貌")],
        description=[zh("地理格局、地貌与环境关系")],
        allowed_taxonomy_node_ids=["tax-geography"],
        attribute_definition_ids=[],
        allowed_relationship_type_ids=[],
        default_view_definition_id="view-geography",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-geology",
        space_id="space-earth",
        key="geology",
        name=[zh("地质与资源")],
        description=[zh("地质构造、岩石与矿产条目")],
        allowed_taxonomy_node_ids=["tax-geology"],
        attribute_definition_ids=[],
        allowed_relationship_type_ids=[],
        default_view_definition_id="view-geology",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-history",
        space_id="space-humanities",
        key="history",
        name=[zh("历史与文明")],
        description=[zh("历史人物、事件与文明演变")],
        allowed_taxonomy_node_ids=["tax-history"],
        attribute_definition_ids=[],
        allowed_relationship_type_ids=[],
        default_view_definition_id="view-history",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-language",
        space_id="space-humanities",
        key="language",
        name=[zh("语言与文字")],
        description=[zh("语系、语法与文字系统")],
        allowed_taxonomy_node_ids=["tax-language"],
        attribute_definition_ids=[],
        allowed_relationship_type_ids=[],
        default_view_definition_id="view-language",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-mathematics",
        space_id="space-mathematics",
        key="mathematics",
        name=[zh("数学条目")],
        description=[zh("数学对象、证明与模型")],
        allowed_taxonomy_node_ids=["tax-mathematics"],
        attribute_definition_ids=[],
        allowed_relationship_type_ids=[],
        default_view_definition_id="view-mathematics",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-medicine",
        space_id="space-medicine",
        key="medicine",
        name=[zh("医学实体")],
        description=[zh("解剖、生理、疾病与治疗")],
        allowed_taxonomy_node_ids=["tax-medicine"],
        attribute_definition_ids=[],
        allowed_relationship_type_ids=[],
        default_view_definition_id="view-medicine",
        schema_version="schema-2.1.0",
    ),
    EntityType(
        id="type-sea-feature",
        space_id="space-oceanography",
        key="sea-feature",
        name=[zh("海洋对象")],
        description=[zh("可被检索和引用的海洋地形与观测对象")],
        allowed_taxonomy_node_ids=["tax-marine-objects", "tax-deep-sea"],
        attribute_definition_ids=["attr-sea-depth"],
        allowed_relationship_type_ids=["rel-in-depth-of"],
        default_view_definition_id="view-sea-feature-detail",
        schema_version="schema-1.0.0",
    ),
]

VIEW_DEFINITIONS = [
    ViewDefinition(
        id="view-animal",
        entity_type_id="type-animal",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={"showAliases": True}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="classification",
                type="classification",
                config={
                    "depth": 8,
                    "rows": [
                        ["界", "动物界"],
                        ["门", "脊索动物门"],
                        ["纲", "哺乳纲"],
                        ["目", "食肉目"],
                        ["科", "猫科"],
                        ["属", "豹属"],
                    ],
                },
            ),
            ViewBlock(id="attributes", type="attribute-table", config={"group": "biology"}),
            ViewBlock(id="map", type="map", title=[zh("分布与栖息地")], config={}),
            ViewBlock(id="relations", type="relationship-list", config={}),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-plant",
        entity_type_id="type-plant",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="classification",
                type="classification",
                config={
                    "depth": 8,
                    "rows": [
                        ["界", "植物界"],
                        ["门", "银杏门"],
                        ["纲", "银杏纲"],
                        ["目", "银杏目"],
                        ["科", "银杏科"],
                    ],
                },
            ),
            ViewBlock(
                id="timeline",
                type="timeline",
                title=[zh("演化与人类利用")],
                config={
                    "events": [
                        ["约 2.7 亿年前", "银杏类植物的早期化石记录"],
                        ["18 世纪", "银杏逐步进入欧洲植物学研究"],
                    ]
                },
            ),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-electronic-component",
        entity_type_id="type-electronic-component",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={"showIdentifiers": True}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(id="attributes", type="attribute-table", config={"group": "electrical"}),
            ViewBlock(id="relations", type="relationship-list", config={"groupByType": True}),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-geography",
        entity_type_id="type-geography",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="location-map",
                type="map",
                title=[zh("地理与地形")],
                config={},
            ),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-geology",
        entity_type_id="type-geology",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="timeline",
                type="timeline",
                title=[zh("地质演化")],
                config={},
            ),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-history",
        entity_type_id="type-history",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="timeline",
                type="timeline",
                title=[zh("历史节点")],
                config={},
            ),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-language",
        entity_type_id="type-language",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(id="attributes", type="attribute-table", config={}),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-mathematics",
        entity_type_id="type-mathematics",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="classification",
                type="classification",
                title=[zh("数学维度")],
                config={
                    "rows": [
                        ["对象", "定义"],
                        ["价值", "建模"],
                        ["应用", "工程"],
                    ],
                },
            ),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-medicine",
        entity_type_id="type-medicine",
        schema_version="schema-2.0.1",
        blocks=[
            ViewBlock(id="hero", type="hero", config={}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="timeline",
                type="timeline",
                title=[zh("机制与流程")],
                config={},
            ),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
    ViewDefinition(
        id="view-sea-feature-detail",
        entity_type_id="type-sea-feature",
        schema_version="schema-1.0.0",
        blocks=[
            ViewBlock(id="hero", type="hero", config={"showAliases": True}),
            ViewBlock(id="summary", type="summary", config={}),
            ViewBlock(
                id="location-map",
                type="map",
                title=[zh("空间与地理")],
                config={},
            ),
            ViewBlock(
                id="attribute-table",
                type="attribute-table",
                config={"group": "physical"},
            ),
            ViewBlock(id="relations", type="relationship-list", config={"groupByType": True}),
            ViewBlock(id="citations", type="citations", config={}),
        ],
    ),
]

CITATIONS = {
    "snow-leopard": Citation(
        id="cite-snow-leopard-001",
        source_id="source-iucn-fixture",
        source_title="IUCN Red List species assessment — deterministic fixture",
        source_tier="authoritative",
        retrieved_at=NOW,
        locator="species account",
    ),
    "ginkgo": Citation(
        id="cite-ginkgo-001",
        source_id="source-flora-fixture",
        source_title="Flora reference — deterministic fixture",
        source_tier="authoritative",
        retrieved_at=NOW,
        locator="Ginkgoaceae",
    ),
    "ne555": Citation(
        id="cite-ne555-001",
        source_id="source-datasheet-fixture",
        source_title="NE555 data sheet — deterministic fixture",
        source_tier="primary",
        retrieved_at=NOW,
        locator="recommended operating conditions",
    ),
    "mariana-trench": Citation(
        id="cite-mariana-trench-001",
        source_id="source-oceanography-fixture",
        source_title="Mariana Trench bathymetry observation — deterministic fixture",
        source_tier="authoritative",
        retrieved_at=NOW,
        locator="bathymetric profile",
    ),
}


def entity(
    *,
    slug: str,
    type_id: str,
    name: str,
    english_name: str,
    alias: str,
    alias_locale: str,
    description: str,
    english_description: str,
    taxonomy_node_id: str,
    attribute_id: str,
    value: str,
    section_heading: str,
    english_section_heading: str,
    section_body: str,
    english_section_body: str,
    revision_sequence: str = "001",
) -> KnowledgeEntity:
    ref = EntityRef(id=f"entity-{slug}", slug=slug, type_id=type_id, canonical_name=name)
    citation = CITATIONS[slug]
    revision = RevisionContext(
        revision_id=f"rev-{slug}-2026-07-29-{revision_sequence}",
        data_version="atlas-2026.07.29",
        schema_version="schema-2.0.0",
        policy_version="policy-1.0.0",
        created_at=NOW,
    )
    return KnowledgeEntity(
        ref=ref,
        names=[zh(name), en(english_name)],
        aliases=[LocalizedText(locale=alias_locale, value=alias)],
        description=[zh(description), en(english_description)],
        taxonomy_node_ids=[taxonomy_node_id],
        claims=[
            ClaimValue(
                id=f"claim-{slug}-001",
                attribute_definition_id=attribute_id,
                original_value=value,
                normalized_value=value,
                display_value=[zh(value)],
                confidence=0.98,
                citation_ids=[citation.id],
                revision_id=revision.revision_id,
            )
        ],
        sections=[
            ContentSection(
                id=f"section-{slug}-overview",
                key="overview",
                heading=[zh(section_heading), en(english_section_heading)],
                body=[zh(section_body), en(english_section_body)],
                citation_ids=[citation.id],
                order=10,
            )
        ],
        relationships=[],
        citations=[citation],
        revision=revision,
        publication_status="published",
    )


ENTITIES = [
    entity(
        slug="snow-leopard",
        type_id="type-animal",
        name="雪豹",
        english_name="Snow leopard",
        alias="Panthera uncia",
        alias_locale="la",
        description="生活在亚洲中部高山生态系统的大型猫科动物。",
        english_description=(
            "A large felid native to the high-mountain ecosystems of Central Asia."
        ),
        taxonomy_node_id="tax-animals",
        attribute_id="attr-scientific-name",
        value="Panthera uncia",
        section_heading="概述",
        english_section_heading="Overview",
        section_body="雪豹适应寒冷、干燥和高海拔环境，是高山生态系统的重要指示物种。",
        english_section_body=(
            "The snow leopard is adapted to cold, arid, high-elevation habitats "
            "and is an important indicator species of mountain ecosystems."
        ),
        revision_sequence="002",
    ),
    entity(
        slug="ginkgo",
        type_id="type-plant",
        name="银杏",
        english_name="Ginkgo",
        alias="Ginkgo biloba",
        alias_locale="la",
        description="银杏纲现存唯一代表物种，具有悠久的演化历史。",
        english_description=(
            "The only living representative of Ginkgoopsida, with a long "
            "evolutionary history."
        ),
        taxonomy_node_id="tax-plants",
        attribute_id="attr-scientific-name",
        value="Ginkgo biloba",
        section_heading="形态与演化",
        english_section_heading="Morphology and evolution",
        section_body="银杏具有扇形叶和独特的二歧脉序，化石记录显示其谱系历史悠久。",
        english_section_body=(
            "Ginkgo has fan-shaped leaves with distinctive dichotomous venation; "
            "its fossil record documents an ancient lineage."
        ),
    ),
    entity(
        slug="ne555",
        type_id="type-electronic-component",
        name="NE555 定时器",
        english_name="NE555 timer",
        alias="555 timer",
        alias_locale="en",
        description="常用于延时、振荡和脉冲产生的通用集成电路。",
        english_description=(
            "A general-purpose integrated circuit used for delays, oscillation, "
            "and pulse generation."
        ),
        taxonomy_node_id="tax-electronics",
        attribute_id="attr-supply-voltage",
        value="4.5–16 V",
        section_heading="功能",
        english_section_heading="Function",
        section_body="器件通过内部比较器、触发器和放电晶体管构成可配置定时电路。",
        english_section_body=(
            "Internal comparators, a latch, and a discharge transistor form a "
            "configurable timing circuit."
        ),
        revision_sequence="002",
    ),
    entity(
        slug="mariana-trench",
        type_id="type-sea-feature",
        name="马里亚纳海沟",
        english_name="Mariana Trench",
        alias="Mariana Trench",
        alias_locale="en",
        description="世界上已知最深的海沟，连接马里亚纳海槽与周边深海地形。",
        english_description=(
            "The Mariana Trench is the deepest known oceanic trench on Earth "
            "with the Challenger Deep as its deepest section."
        ),
        taxonomy_node_id="tax-deep-sea",
        attribute_id="attr-sea-depth",
        value="10984 m",
        section_heading="测量与地理",
        english_section_heading="Bathymetry and location",
        section_body="马里亚纳海沟位于西太平洋，挑战者深渊最深点约为 10984 米。"
        " 其海域与板块俯冲边界相关。",
        english_section_body=(
            "Located in the western Pacific Ocean, the Mariana Trench is "
            "associated with the Mariana subduction zone. The Challenger Deep "
            "reaches approximately 10,984 meters."
        ),
    ),
]

ENTITIES[2].claims[0] = ENTITIES[2].claims[0].model_copy(
    update={"normalized_value": {"min": 4.5, "max": 16, "unit": "V"}}
)
ENTITIES[3].claims[0] = ENTITIES[3].claims[0].model_copy(
    update={"normalized_value": {"min": 0.0, "max": 10984.0, "unit": "m"}}
)

ENTITIES[0].claims.append(
    ClaimValue(
        id="claim-snow-leopard-conservation",
        attribute_definition_id="attr-conservation",
        original_value="EN",
        normalized_value="EN",
        display_value=[zh("濒危（EN）")],
        confidence=0.94,
        citation_ids=["cite-snow-leopard-001"],
        revision_id=ENTITIES[0].revision.revision_id,
    )
)

AGENT_RUNS = [
    AgentRun(
        id="run-source-monitor-2841",
        role=AgentRole.SOURCE_MONITOR,
        status="completed",
        model_version="tool-only",
        prompt_version="monitor-1.2.0",
        policy_version="policy-1.0.0",
        input_source_ids=["source-iucn-fixture", "source-datasheet-fixture"],
        proposal_ids=["proposal-snow-leopard-status"],
        started_at=NOW,
        completed_at=NOW,
    ),
    AgentRun(
        id="run-entity-resolution-2842",
        role=AgentRole.ENTITY_RESOLUTION,
        status="waiting-review",
        model_version="fixture-model",
        prompt_version="resolve-2.1.0",
        policy_version="policy-1.0.0",
        input_source_ids=["source-flora-fixture"],
        proposal_ids=["proposal-ginkgo-alias"],
        started_at=NOW,
    ),
]

PROPOSALS = [
    AgentProposal(
        id="proposal-snow-leopard-status",
        entity_id="entity-snow-leopard",
        proposal_type="content",
        operations=[
            ChangeOperation(
                operation="replace",
                path="/claims/attr-conservation",
                before="EN",
                after="VU",
                citation_ids=["cite-snow-leopard-001"],
                confidence=0.96,
            )
        ],
        risk="high",
        status="human-review",
        agent_run_id="run-source-monitor-2841",
        impact={"entityCount": 1, "relationCount": 6, "localeCount": 4},
    )
]
