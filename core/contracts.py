"""Small application contracts; no UI, graph or model dependencies."""
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pyproj import CRS

UNSPECIFIED_CRS = object()


def normalize_crs(value):
    """Validate a CRS value, without projecting or importing application GIS."""
    if value is None:
        return None
    try:
        result = CRS.from_user_input(value)
        if not (result.is_geographic or result.is_projected):
            raise ValueError("unsupported coordinate system")
        return result.to_string()
    except Exception as exc:
        raise ValueError("CRS nicht erkannt oder nicht unterstützt.") from exc


def data_crs(data, *, context=UNSPECIFIED_CRS):
    """Read native/header contract declarations; no coordinate interpretation.

    Missing headers use the existing GeoJSON/lat-lon wire protocol. Explicit
    null/native None remains unknown. Properties dictionaries are not headers.
    GIS I/O independently validates coordinates before analysis or rendering.
    """
    if hasattr(data, "geometry"):
        return normalize_crs(data.crs)
    declarations = []
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if "lat" in value and "lon" in value and "geometry" not in value:
                declarations.append("EPSG:4326")
                return
            if "crs" in value:
                header = value["crs"]
                if header is None:
                    declarations.append(None)
                elif isinstance(header, dict) and header.get("type") == "name":
                    properties = header.get("properties")
                    name = properties.get("name") if isinstance(properties, dict) else None
                    if not isinstance(name, str) or not name.upper().startswith(("EPSG:", "URN:OGC:DEF:CRS:", "OGC:")):
                        raise ValueError("CRS nicht unterstützt.")
                    declarations.append(normalize_crs(name))
                else:
                    raise ValueError("CRS nicht unterstützt (named CRS erforderlich).")
            for key in ("features", "geometry", "geometries"):
                if key in value:
                    visit(value[key])
    visit(data)
    if not declarations:
        supported = ((isinstance(data, dict) and (data.get("type") in ("Feature", "FeatureCollection") or "features" in data))
                     or (isinstance(data, list) and all(isinstance(item, dict) and (
                         "geometry" in item or ("lat" in item and "lon" in item)) for item in data)))
        return UNSPECIFIED_CRS if supported else None
    if not (isinstance(data, dict) and "crs" in data):
        declarations.insert(0, "EPSG:4326" if context is UNSPECIFIED_CRS else normalize_crs(context))
    first = declarations[0]
    if any((item is None) != (first is None) or (
            item is not None and not CRS(item).equals(CRS(first), ignore_axis_order=True))
           for item in declarations[1:]):
        raise ValueError("CRS-Konflikt in den Daten.")
    return first


def validate_dataset_crs(dataset):
    expected = normalize_crs(dataset.crs)
    actual = data_crs(dataset.data, context=expected)
    if actual is not UNSPECIFIED_CRS and ((actual is None) != (expected is None) or (
            actual is not None and not CRS(actual).equals(CRS(expected), ignore_axis_order=True))):
        raise ValueError("CRS-Konflikt zwischen Dataset und Daten.")
    return expected


def new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class Dataset:
    """Own source coordinates; crs describes those coordinates, not map output."""
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
