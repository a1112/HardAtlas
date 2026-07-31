from datetime import UTC, datetime

from hardatlas_domain import EvidenceRef, ProductRef, SpecificationValue

CPU = ProductRef(model_id="cpu-amd-ryzen-9-9950x", sku_id="100-100001277WOF")
BOARD = ProductRef(model_id="mb-asus-proart-x870e-creator", revision_id="1.0")
GPU = ProductRef(model_id="gpu-nvidia-rtx-5090", sku_id="fixture-reference")
PSU = ProductRef(model_id="psu-corsair-hx1500i-2023")

PRODUCTS = [
    {
        "ref": CPU,
        "name": "AMD Ryzen 9 9950X",
        "category": "桌面处理器",
        "manufacturer": "AMD",
        "aliases": ["9950X", "Granite Ridge 16C"],
    },
    {
        "ref": BOARD,
        "name": "ProArt X870E-CREATOR WIFI",
        "category": "ATX 主板",
        "manufacturer": "ASUS",
        "aliases": ["X870E Creator"],
    },
    {
        "ref": GPU,
        "name": "GeForce RTX 5090",
        "category": "桌面显卡",
        "manufacturer": "NVIDIA",
        "aliases": ["RTX5090"],
    },
    {
        "ref": PSU,
        "name": "HX1500i",
        "category": "ATX 电源",
        "manufacturer": "Corsair",
        "aliases": ["HX 1500i"],
    },
]

EVIDENCE = EvidenceRef(
    id="ev-fixture-manual-001",
    title="确定性夹具：主板 CPU 支持表",
    source_level="A",
    url="https://example.invalid/fixtures/board-support",
    retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    applies_to=BOARD,
)

SPECIFICATIONS = [
    SpecificationValue(
        definition_id="socket",
        original_value="AM5",
        normalized_value="AM5",
        display_value="AM5",
        applies_to=BOARD,
        evidence=[EVIDENCE],
        confidence=1,
    ),
    SpecificationValue(
        definition_id="memory.max_capacity",
        original_value="192 GB",
        normalized_value=192,
        display_value="192 GB",
        unit="GB",
        applies_to=BOARD,
        evidence=[EVIDENCE],
        confidence=1,
    ),
]
