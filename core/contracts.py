"""Small application contracts; no UI, graph or model dependencies."""
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class Dataset:
    name: str
    data: Any
    crs: str | None = "EPSG:4326"
    source: str = "upload"
    parent_ids: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    dataset_id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class Layer:
    dataset_id: str
    name: str
    renderer: str = "geojson"
    data_field: str | None = None
    style: dict = field(default_factory=dict)
    layer_id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class ToolResult:
    success: bool
    message: str
    dataset_ids: tuple[str, ...] = ()
    layer_ids: tuple[str, ...] = ()
    updated_layer_ids: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error_category: str | None = None
