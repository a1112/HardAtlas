import type {
  KnowledgeEntity,
  TaxonomyNode,
  ViewDefinition,
} from "@hardatlas/contracts";

export const taxonomyNodes: TaxonomyNode[] = [
  {
    id: "tax-animals",
    spaceId: "space-life",
    slug: "animals",
    name: [{ locale: "zh-CN", value: "动物" }],
    parentIds: [],
    childCount: 14,
    entityCount: 482316,
    pathKeys: ["生命科学", "动物"],
  },
  {
    id: "tax-plants",
    spaceId: "space-life",
    slug: "plants",
    name: [{ locale: "zh-CN", value: "植物" }],
    parentIds: [],
    childCount: 11,
    entityCount: 391084,
    pathKeys: ["生命科学", "植物"],
  },
  {
    id: "tax-electronics",
    spaceId: "space-engineering",
    slug: "electronic-components",
    name: [{ locale: "zh-CN", value: "电子元件" }],
    parentIds: [],
    childCount: 18,
    entityCount: 126540,
    pathKeys: ["工程技术", "电子元件"],
  },
  {
    id: "tax-geography",
    spaceId: "space-earth",
    slug: "geography",
    name: [{ locale: "zh-CN", value: "地理" }],
    parentIds: [],
    childCount: 12,
    entityCount: 120_100,
    pathKeys: ["地球与环境", "地理"],
  },
  {
    id: "tax-geology",
    spaceId: "space-earth",
    slug: "geology",
    name: [{ locale: "zh-CN", value: "地质" }],
    parentIds: [],
    childCount: 7,
    entityCount: 48_200,
    pathKeys: ["地球与环境", "地质"],
  },
  {
    id: "tax-history",
    spaceId: "space-humanities",
    slug: "history",
    name: [{ locale: "zh-CN", value: "历史" }],
    parentIds: [],
    childCount: 9,
    entityCount: 210_000,
    pathKeys: ["人文与历史", "历史"],
  },
  {
    id: "tax-language",
    spaceId: "space-humanities",
    slug: "language",
    name: [{ locale: "zh-CN", value: "语言与文字" }],
    parentIds: [],
    childCount: 16,
    entityCount: 75_300,
    pathKeys: ["人文与历史", "语言与文字"],
  },
  {
    id: "tax-mathematics",
    spaceId: "space-mathematics",
    slug: "mathematics",
    name: [{ locale: "zh-CN", value: "数学" }],
    parentIds: [],
    childCount: 14,
    entityCount: 80_120,
    pathKeys: ["数学与逻辑", "数学"],
  },
  {
    id: "tax-medicine",
    spaceId: "space-medicine",
    slug: "medicine",
    name: [{ locale: "zh-CN", value: "医学" }],
    parentIds: [],
    childCount: 17,
    entityCount: 310_430,
    pathKeys: ["医学与健康", "医学"],
  },
  {
    id: "tax-astronomy",
    spaceId: "space-astronomy",
    slug: "astronomy",
    name: [{ locale: "zh-CN", value: "天文与空间" }],
    parentIds: [],
    childCount: 2,
    entityCount: 2,
    pathKeys: ["天文与空间", "天文与空间"],
  },
  {
    id: "tax-stars",
    spaceId: "space-astronomy",
    slug: "stars",
    name: [{ locale: "zh-CN", value: "恒星" }],
    parentIds: ["tax-astronomy"],
    childCount: 0,
    entityCount: 1,
    pathKeys: ["天文与空间", "天文与空间", "恒星"],
  },
  {
    id: "tax-planets",
    spaceId: "space-astronomy",
    slug: "planets",
    name: [{ locale: "zh-CN", value: "行星" }],
    parentIds: ["tax-astronomy"],
    childCount: 0,
    entityCount: 1,
    pathKeys: ["天文与空间", "天文与空间", "行星"],
  },
  {
    id: "tax-marine-objects",
    spaceId: "space-oceanography",
    slug: "marine-objects",
    name: [{ locale: "zh-CN", value: "海洋对象" }],
    parentIds: [],
    childCount: 2,
    entityCount: 1,
    pathKeys: ["海洋与水圈", "海洋对象"],
  },
  {
    id: "tax-deep-sea",
    spaceId: "space-oceanography",
    slug: "deep-sea",
    name: [{ locale: "zh-CN", value: "深海地形" }],
    parentIds: ["tax-marine-objects"],
    childCount: 0,
    entityCount: 1,
    pathKeys: ["海洋与水圈", "海洋对象", "深海地形"],
  },
];

const revision = {
  revisionId: "rev-2026-07-28-001",
  dataVersion: "atlas-2026.07.28",
  schemaVersion: "schema-2.0.0",
  policyVersion: "policy-1.0.0",
  createdAt: "2026-07-28T12:00:00Z",
};

export const entities: KnowledgeEntity[] = [
  {
    ref: {
      id: "entity-snow-leopard",
      slug: "snow-leopard",
      typeId: "type-animal",
      canonicalName: "雪豹",
    },
    names: [{ locale: "zh-CN", value: "雪豹" }],
    aliases: [
      { locale: "la", value: "Panthera uncia" },
      { locale: "en", value: "Snow leopard" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "生活在亚洲中部高山生态系统的大型猫科动物。",
      },
    ],
    taxonomyNodeIds: ["tax-animals"],
    claims: [
      {
        id: "claim-snow-scientific-name",
        attributeDefinitionId: "scientific_name",
        originalValue: "Panthera uncia",
        normalizedValue: "Panthera uncia",
        displayValue: [{ locale: "zh-CN", value: "Panthera uncia" }],
        confidence: 0.99,
        citationIds: ["cite-snow-001"],
        revisionId: revision.revisionId,
      },
      {
        id: "claim-snow-status",
        attributeDefinitionId: "conservation_status",
        originalValue: "Vulnerable",
        normalizedValue: "VU",
        displayValue: [{ locale: "zh-CN", value: "易危（VU）" }],
        confidence: 0.96,
        citationIds: ["cite-snow-001"],
        revisionId: revision.revisionId,
      },
      {
        id: "claim-snow-elevation",
        attributeDefinitionId: "elevation_range",
        originalValue: "3000–5000 m",
        normalizedValue: { min: 3000, max: 5000 },
        displayValue: [{ locale: "zh-CN", value: "3,000–5,000 米" }],
        unit: "m",
        confidence: 0.91,
        citationIds: ["cite-snow-002"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "snow-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "概述" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "雪豹适应寒冷、干燥和高海拔环境，是亚洲中部高山生态系统的重要指示物种。其宽大的足部、浓密皮毛和长尾有助于在陡峭雪地中活动。",
          },
        ],
        citationIds: ["cite-snow-001", "cite-snow-002"],
        order: 10,
      },
      {
        id: "snow-habitat",
        key: "habitat",
        heading: [{ locale: "zh-CN", value: "分布与栖息地" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "主要分布于喜马拉雅、青藏高原、帕米尔和天山等山系，偏好岩石裸露、坡度较大且植被稀疏的高山区域。",
          },
        ],
        citationIds: ["cite-snow-002"],
        order: 20,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-snow-001",
        sourceId: "source-iucn",
        sourceTitle: "IUCN Red List — Panthera uncia",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
        locator: "Species assessment",
      },
      {
        id: "cite-snow-002",
        sourceId: "source-catsg",
        sourceTitle: "IUCN/SSC Cat Specialist Group — Snow Leopard",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
        locator: "Distribution and habitat",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-ginkgo",
      slug: "ginkgo",
      typeId: "type-plant",
      canonicalName: "银杏",
    },
    names: [{ locale: "zh-CN", value: "银杏" }],
    aliases: [
      { locale: "la", value: "Ginkgo biloba" },
      { locale: "zh-CN", value: "白果树" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "银杏纲现存唯一代表物种，具有悠久的演化历史。",
      },
    ],
    taxonomyNodeIds: ["tax-plants"],
    claims: [
      {
        id: "claim-ginkgo-scientific-name",
        attributeDefinitionId: "scientific_name",
        originalValue: "Ginkgo biloba",
        normalizedValue: "Ginkgo biloba",
        displayValue: [{ locale: "zh-CN", value: "Ginkgo biloba" }],
        confidence: 0.99,
        citationIds: ["cite-ginkgo-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "ginkgo-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "概述" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "银杏具有扇形叶和独特的二歧脉序。现生银杏常作为研究种子植物演化、城市生态和传统利用的重要对象。",
          },
        ],
        citationIds: ["cite-ginkgo-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-ginkgo-001",
        sourceId: "source-flora",
        sourceTitle: "Flora of China — Ginkgoaceae",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-ne555",
      slug: "ne555",
      typeId: "type-electronic-component",
      canonicalName: "NE555 定时器",
    },
    names: [{ locale: "zh-CN", value: "NE555 定时器" }],
    aliases: [
      { locale: "en", value: "555 timer" },
      { locale: "en", value: "NE555P" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "常用于延时、振荡和脉冲产生的通用集成电路。",
      },
    ],
    taxonomyNodeIds: ["tax-electronics"],
    claims: [
      {
        id: "claim-ne555-voltage",
        attributeDefinitionId: "supply_voltage",
        originalValue: "4.5 V to 16 V",
        normalizedValue: { min: 4.5, max: 16 },
        displayValue: [{ locale: "zh-CN", value: "4.5–16 V" }],
        unit: "V",
        confidence: 1,
        citationIds: ["cite-ne555-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "ne555-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "功能" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "NE555 通过内部比较器、SR 锁存器和放电晶体管构成可配置定时电路，可工作于单稳态、无稳态等模式。",
          },
        ],
        citationIds: ["cite-ne555-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-ne555-001",
        sourceId: "source-ne555-datasheet",
        sourceTitle: "NE555 Precision Timers Data Sheet",
        sourceTier: "primary",
        retrievedAt: "2026-07-28T12:00:00Z",
        locator: "Recommended operating conditions",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-himalayan-glacier",
      slug: "himalayan-glacier",
      typeId: "type-geography",
      canonicalName: "喜马拉雅冰川系统",
    },
    names: [{ locale: "zh-CN", value: "喜马拉雅冰川系统" }],
    aliases: [
      { locale: "en", value: "Himalayan glacier system" },
      { locale: "en", value: "Karakoram-Himalaya ice systems" },
    ],
    description: [
      {
        locale: "zh-CN",
        value:
          "覆盖喜马拉雅与喀喇昆仑高山带的大型冰川群，控制着亚洲主要高原水源补给节奏。",
      },
    ],
    taxonomyNodeIds: ["tax-geology"],
    claims: [
      {
        id: "claim-glacier-area",
        attributeDefinitionId: "elevation_range",
        originalValue: "高海拔冰川系统",
        normalizedValue: "high-altitude glacier system",
        displayValue: [{ locale: "zh-CN", value: "高海拔冰川系统" }],
        confidence: 0.93,
        citationIds: ["cite-glacier-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "glacier-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "概述" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "喜马拉雅冰川系统连接区域地理板块运动、季风降水和高原蒸散过程，是研究气候变化响应的重要观测单元。",
          },
        ],
        citationIds: ["cite-glacier-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-glacier-001",
        sourceId: "source-glacier-observatory",
        sourceTitle: "Himalayan Cryosphere Observation Network",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
        locator: "Mass balance report",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-confucius",
      slug: "confucius",
      typeId: "type-history",
      canonicalName: "孔子",
    },
    names: [{ locale: "zh-CN", value: "孔子" }],
    aliases: [
      { locale: "en", value: "Confucius" },
      { locale: "la", value: "Confucius Sapiens" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "春秋末年思想家、教育家，创立儒家学派与完整伦理政治体系。",
      },
    ],
    taxonomyNodeIds: ["tax-history"],
    claims: [
      {
        id: "claim-confucius-era",
        attributeDefinitionId: "scientific_name",
        originalValue: "551 BC - 479 BC",
        normalizedValue: "551-479 BCE",
        displayValue: [
          { locale: "zh-CN", value: "约公元前 551 年至前 479 年" },
        ],
        confidence: 0.86,
        citationIds: ["cite-confucius-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "confucius-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "核心贡献" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "其经典化整理与道德政治框架持续影响中国与东亚多地域的思想体系与教育传统。",
          },
        ],
        citationIds: ["cite-confucius-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-confucius-001",
        sourceId: "source-ancient-history",
        sourceTitle: "Institute for Chinese Classics — Confucius Index",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
        locator: "Biographical dataset",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-prime-number",
      slug: "prime-number",
      typeId: "type-mathematics",
      canonicalName: "素数",
    },
    names: [{ locale: "zh-CN", value: "素数" }],
    aliases: [
      { locale: "en", value: "Prime number" },
      { locale: "la", value: "Numerus Primus" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "大于 1 的自然数，除 1 和自身外没有其他正因数。",
      },
    ],
    taxonomyNodeIds: ["tax-mathematics"],
    claims: [
      {
        id: "claim-prime-def",
        attributeDefinitionId: "scientific_name",
        originalValue: ">1 的自然数且仅有 1 与自身为正因数",
        normalizedValue: ">1 and divisible only by 1 and itself",
        displayValue: [
          { locale: "zh-CN", value: "大于 1 且仅被 1 和自身整除" },
        ],
        confidence: 0.99,
        citationIds: ["cite-prime-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "prime-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "定义与意义" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "素数是数论中最基本的结构对象之一，也是密码学和离散数学中的核心基元。",
          },
        ],
        citationIds: ["cite-prime-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-prime-001",
        sourceId: "source-math-handbook",
        sourceTitle: "Mathematics Reference Ledger",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-medicine-immune-system",
      slug: "immune-system",
      typeId: "type-medicine",
      canonicalName: "先天免疫系统",
    },
    names: [{ locale: "zh-CN", value: "先天免疫系统" }],
    aliases: [
      { locale: "en", value: "Innate immune system" },
      { locale: "en", value: "Innate immunity" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "生物体在接触病原体时立即触发的非特异性免疫防线。",
      },
    ],
    taxonomyNodeIds: ["tax-medicine"],
    claims: [
      {
        id: "claim-immune-speed",
        attributeDefinitionId: "scientific_name",
        originalValue: "Rapid, non-specific defense",
        normalizedValue: "rapid non-specific defense",
        displayValue: [{ locale: "zh-CN", value: "快速、非特异性免疫防御" }],
        confidence: 0.97,
        citationIds: ["cite-immune-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "immune-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "关键机制" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "通过先天免疫细胞识别病原分子模式，快速限制病原扩散并触发后续适应性免疫反应。",
          },
        ],
        citationIds: ["cite-immune-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-immune-001",
        sourceId: "source-immunology-brief",
        sourceTitle: "Clinical Immunology Digest",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-sun",
      slug: "sun",
      typeId: "type-celestial-body",
      canonicalName: "太阳",
    },
    names: [{ locale: "zh-CN", value: "太阳" }],
    aliases: [
      { locale: "en", value: "Sun" },
      { locale: "en", value: "Sol" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "太阳系中心恒星，太阳辐射驱动地球季节、气候与生命代谢。",
      },
    ],
    taxonomyNodeIds: ["tax-stars"],
    claims: [
      {
        id: "claim-sun-type",
        attributeDefinitionId: "scientific_name",
        originalValue: "G2V",
        normalizedValue: "G2V",
        displayValue: [{ locale: "zh-CN", value: "G2V（黄矮星）" }],
        confidence: 0.95,
        citationIds: ["cite-sun-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "sun-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "天体属性" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "太阳是典型的黄矮星，持续的核聚变释放光与热，是太阳系能量和空间天气活动的核心来源。",
          },
        ],
        citationIds: ["cite-sun-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-sun-001",
        sourceId: "source-astronomy",
        sourceTitle: "Global Solar Observatory Index",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-mars",
      slug: "mars",
      typeId: "type-celestial-body",
      canonicalName: "火星",
    },
    names: [{ locale: "zh-CN", value: "火星" }],
    aliases: [
      { locale: "en", value: "Mars" },
      { locale: "en", value: "The Red Planet" },
    ],
    description: [
      {
        locale: "zh-CN",
        value:
          "第四颗行星，火星气候与地质演化对近地系研究和行星探测具有核心价值。",
      },
    ],
    taxonomyNodeIds: ["tax-planets"],
    claims: [
      {
        id: "claim-mars-type",
        attributeDefinitionId: "scientific_name",
        originalValue: "Classical planet",
        normalizedValue: "terrestrial planet",
        displayValue: [{ locale: "zh-CN", value: "类地行星" }],
        confidence: 0.94,
        citationIds: ["cite-mars-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "mars-overview",
        key: "overview",
        heading: [{ locale: "zh-CN", value: "探索与特征" }],
        body: [
          {
            locale: "zh-CN",
            value:
              "火星拥有稀薄大气与极冠冰冠，现代探测持续修正其古环境演化模型。",
          },
        ],
        citationIds: ["cite-mars-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-mars-001",
        sourceId: "source-astronomy",
        sourceTitle: "Solar System Survey Bureau",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
      },
    ],
    revision,
    publicationStatus: "published",
  },
  {
    ref: {
      id: "entity-mariana-trench",
      slug: "mariana-trench",
      typeId: "type-sea-feature",
      canonicalName: "马里亚纳海沟",
    },
    names: [
      { locale: "zh-CN", value: "马里亚纳海沟" },
      { locale: "en", value: "Mariana Trench" },
    ],
    aliases: [
      { locale: "la", value: "Fossa Mariana" },
      { locale: "en", value: "Mariana Trench" },
    ],
    description: [
      {
        locale: "zh-CN",
        value: "世界上已知最深的海沟，连接马里亚纳海槽与周边深海地形系统。",
      },
    ],
    taxonomyNodeIds: ["tax-deep-sea"],
    claims: [
      {
        id: "claim-mariana-depth",
        attributeDefinitionId: "max_depth_meters",
        originalValue: "10984 米",
        normalizedValue: 10_984,
        displayValue: [
          { locale: "zh-CN", value: "10984 米" },
          { locale: "en", value: "10984 m" },
        ],
        confidence: 0.99,
        citationIds: ["cite-mariana-trench-001"],
        revisionId: revision.revisionId,
      },
    ],
    sections: [
      {
        id: "mariana-overview",
        key: "overview",
        heading: [
          { locale: "zh-CN", value: "地理与测量" },
          { locale: "en", value: "Geography and bathymetry" },
        ],
        body: [
          {
            locale: "zh-CN",
            value:
              "马里亚纳海沟位于西太平洋，是板块俯冲造山带的重要沉积与形变示例。",
          },
          {
            locale: "en",
            value:
              "The Mariana Trench is in the western Pacific and is shaped by active subduction tectonics.",
          },
        ],
        citationIds: ["cite-mariana-trench-001"],
        order: 10,
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-mariana-trench-001",
        sourceId: "source-oceanography",
        sourceTitle:
          "Oceanography Observation Center — Mariana Trench Bathymetry",
        sourceTier: "authoritative",
        retrievedAt: "2026-07-28T12:00:00Z",
        locator: "bathymetric core profile",
      },
    ],
    revision,
    publicationStatus: "published",
  },
];

export const viewDefinitions: Record<string, ViewDefinition> = {
  "type-animal": {
    id: "view-animal",
    entityTypeId: "type-animal",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: { showAliases: true } },
      { id: "summary", type: "summary", config: {} },
      {
        id: "classification",
        type: "classification",
        title: [{ locale: "zh-CN", value: "科学分类" }],
        config: {
          rows: [
            ["界", "动物界"],
            ["门", "脊索动物门"],
            ["纲", "哺乳纲"],
            ["目", "食肉目"],
            ["科", "猫科"],
            ["属", "豹属"],
          ],
        },
      },
      {
        id: "attributes",
        type: "attribute-table",
        title: [{ locale: "zh-CN", value: "关键数据" }],
        config: {},
      },
      {
        id: "map",
        type: "map",
        title: [{ locale: "zh-CN", value: "分布与栖息地" }],
        config: {},
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-plant": {
    id: "view-plant",
    entityTypeId: "type-plant",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: {} },
      { id: "summary", type: "summary", config: {} },
      {
        id: "timeline",
        type: "timeline",
        title: [{ locale: "zh-CN", value: "演化与人类利用" }],
        config: {
          events: [
            ["约 2.7 亿年前", "银杏类植物的早期化石记录"],
            ["18 世纪", "银杏逐步进入欧洲植物学研究"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-electronic-component": {
    id: "view-component",
    entityTypeId: "type-electronic-component",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: { showIdentifiers: true } },
      { id: "summary", type: "summary", config: {} },
      {
        id: "attributes",
        type: "attribute-table",
        title: [{ locale: "zh-CN", value: "电气参数" }],
        config: {},
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-geography": {
    id: "view-geography",
    entityTypeId: "type-geography",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: { showAliases: true } },
      { id: "summary", type: "summary", config: {} },
      {
        id: "classification",
        type: "classification",
        title: [{ locale: "zh-CN", value: "空间学科" }],
        config: {
          rows: [
            ["纬度", "球面坐标系"],
            ["高度", "地理高程"],
            ["作用", "环境与水文联系"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-geology": {
    id: "view-geology",
    entityTypeId: "type-geology",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: {} },
      { id: "summary", type: "summary", config: {} },
      {
        id: "timeline",
        type: "timeline",
        title: [{ locale: "zh-CN", value: "地球历史过程" }],
        config: {
          events: [
            ["今", "板块构造与岩浆活动重塑地形"],
            ["古近纪", "重力和侵蚀共同塑造高原边缘"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-history": {
    id: "view-history",
    entityTypeId: "type-history",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: { showAliases: true } },
      { id: "summary", type: "summary", config: {} },
      {
        id: "classification",
        type: "classification",
        title: [{ locale: "zh-CN", value: "史学坐标" }],
        config: {
          rows: [
            ["时代", "中国古代"],
            ["题材", "人物与制度"],
            ["证据", "经典文献"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-language": {
    id: "view-language",
    entityTypeId: "type-language",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: {} },
      { id: "summary", type: "summary", config: {} },
      {
        id: "attributes",
        type: "attribute-table",
        title: [{ locale: "zh-CN", value: "语系与变体" }],
        config: {},
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-mathematics": {
    id: "view-mathematics",
    entityTypeId: "type-mathematics",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: {} },
      { id: "summary", type: "summary", config: {} },
      {
        id: "classification",
        type: "classification",
        title: [{ locale: "zh-CN", value: "数学维度" }],
        config: {
          rows: [
            ["对象", "离散与连续"],
            ["价值", "构造与证明"],
            ["应用", "安全与工程"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-medicine": {
    id: "view-medicine",
    entityTypeId: "type-medicine",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: {} },
      { id: "summary", type: "summary", config: {} },
      {
        id: "timeline",
        type: "timeline",
        title: [{ locale: "zh-CN", value: "机制链路" }],
        config: {
          events: [
            ["识别", "先天通路识别病原模式"],
            ["应答", "炎症介质放大局部防线"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-celestial-body": {
    id: "view-celestial-body",
    entityTypeId: "type-celestial-body",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: { showAliases: true } },
      { id: "summary", type: "summary", config: {} },
      {
        id: "classification",
        type: "classification",
        title: [{ locale: "zh-CN", value: "天文分类" }],
        config: {
          rows: [
            ["系统", "太阳系"],
            ["类别", "天体"],
            ["证据", "权威观测数据库"],
          ],
        },
      },
      {
        id: "timeline",
        type: "timeline",
        title: [{ locale: "zh-CN", value: "观测里程碑" }],
        config: {
          events: [
            ["公元前", "古代肉眼观测与历法应用"],
            ["20-21世纪", "天体望远镜与无人探测升级"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
  "type-sea-feature": {
    id: "view-sea-feature",
    entityTypeId: "type-sea-feature",
    schemaVersion: "schema-2.0.1",
    blocks: [
      { id: "hero", type: "hero", config: { showAliases: true } },
      { id: "summary", type: "summary", config: {} },
      {
        id: "classification",
        type: "classification",
        title: [{ locale: "zh-CN", value: "海洋维度" }],
        config: {
          rows: [
            ["介质", "海水"],
            ["环境", "深海地形"],
            ["证据", "权威测绘数据"],
          ],
        },
      },
      { id: "citations", type: "citations", config: {} },
    ],
  },
};

export function findEntity(slug: string): KnowledgeEntity | undefined {
  return entities.find((entity) => entity.ref.slug === slug);
}

export function searchEntities(query: string): KnowledgeEntity[] {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return entities;
  return entities.filter((entity) =>
    [
      entity.ref.canonicalName,
      ...entity.names.map((item) => item.value),
      ...entity.aliases.map((item) => item.value),
      ...entity.description.map((item) => item.value),
    ].some((value) => value.toLocaleLowerCase().includes(normalized)),
  );
}

export function localize(
  values: Array<{ locale: string; value: string }>,
  locale = "zh-CN",
): string {
  const language = locale.split("-")[0]?.toLowerCase();
  return (
    values.find((item) => item.locale === locale)?.value ??
    values.find((item) => item.locale.split("-")[0]?.toLowerCase() === language)
      ?.value ??
    values.find((item) => item.locale === "zh-CN")?.value ??
    values[0]?.value ??
    ""
  );
}
