"""Explicit in-memory session, defensive reads and single-tool atomic publication.

Legacy dictionaries are transient boundary snapshots, never a second store.
Uploaded JSON/list payloads retain their format; derived frames remain frames.
No geometry validation/projection policy is introduced here.
"""
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
import json
from threading import RLock

from .contracts import Dataset, Layer, ToolResult, new_id


class AmbiguousReference(ValueError):
    pass


class LegacyLayerView(Sequence):
    """Live read-only compatibility projection; each read is a detached snapshot."""
    def __init__(self, session, visible):
        self._session, self._visible = session, visible

    def _snapshot(self):
        return self._session.legacy_layers()[int(self._visible)]

    def __len__(self):
        return len(self._snapshot())

    def __getitem__(self, index):
        return self._snapshot()[index]

    def __iter__(self):
        return iter(self._snapshot())

    def __eq__(self, other):
        return self._snapshot() == list(other)

    def __deepcopy__(self, memo):
        return deepcopy(self._snapshot(), memo)


class SessionData:
    def __init__(self):
        self.session_id = new_id()
        self.lock = RLock()
        self._datasets = {}
        self._layers = {}
        self._uploads = []
        self._results = []
        self.raw_view = LegacyLayerView(self, False)
        self.map_view = LegacyLayerView(self, True)

    @property
    def datasets(self):
        with self.lock:
            return {key: self._copy_dataset(d) for key, d in self._datasets.items()}

    @property
    def layers(self):
        with self.lock:
            return deepcopy(self._layers)

    @property
    def results(self):
        with self.lock:
            return deepcopy(self._results)

    def get_dataset(self, dataset_id):
        with self.lock:
            return self._copy_dataset(self._datasets[dataset_id])

    def get_layer(self, layer_id):
        with self.lock:
            return deepcopy(self._layers[layer_id])

    @staticmethod
    def _copy_dataset(dataset):
        data = deepcopy(dataset.data)
        # pandas deep copies arrays, but object cells can retain mutable Python
        # references. Copy these too without importing pandas or GeoPandas here.
        if hasattr(data, "geometry") and hasattr(data, "columns"):
            for column in data.columns:
                if column != data.geometry.name and data[column].dtype == object:
                    data[column] = data[column].map(deepcopy)
        return replace(dataset, data=data, metadata=deepcopy(dataset.metadata))

    @staticmethod
    def _payload(dataset):
        data = dataset.data
        if hasattr(data, "to_json") and hasattr(data, "geometry"):
            value = json.loads(data.to_json())
            return value["features"] if dataset.metadata.get("format") == "features" else value
        return deepcopy(data)

    def legacy_layers(self):
        with self.lock:
            payloads = {key: self._payload(d) for key, d in self._datasets.items()}
            raw = []
            for key in self._uploads:
                d = self._datasets[key]
                raw.append({**deepcopy(d.metadata), "name": d.name, "type": d.metadata.get("type", "geojson"),
                            "data": payloads[key], "dataset_id": key})
            shown = [{"name": l.name, "type": l.renderer, "data": payloads[l.dataset_id],
                      "data_field": l.data_field, "folium_kwargs": deepcopy(l.style),
                      "layer_id": l.layer_id, "dataset_id": l.dataset_id}
                     for l in self._layers.values()]
            return raw, shown

    def get_agent_layers(self):
        return self.raw_view, self.map_view

    def add_dataset(self, dataset, *, uploaded=False):
        with self.lock:
            if dataset.dataset_id in self._datasets:
                raise ValueError("Dataset ID already exists")
            if any(key not in self._datasets for key in dataset.parent_ids):
                raise ValueError("Unknown parent dataset")
            self._datasets[dataset.dataset_id] = self._copy_dataset(dataset)
            if uploaded:
                self._uploads.append(dataset.dataset_id)
            return dataset.dataset_id

    def add_upload(self, name, data, *, file_id=None, renderer="geojson", data_fields=()):
        crs = str(data.crs) if getattr(data, "crs", None) is not None else (
            None if hasattr(data, "geometry") else "EPSG:4326")
        d = Dataset(name, data, crs=crs, source=file_id or "upload", metadata={
            "type": renderer, "file_id": file_id or name, "data_fields": list(data_fields)})
        return self.add_dataset(d, uploaded=True)

    def add_layer(self, layer):
        with self.lock:
            if layer.dataset_id not in self._datasets or layer.layer_id in self._layers:
                raise ValueError("Unknown dataset or duplicate layer ID")
            self._layers[layer.layer_id] = deepcopy(layer)
            return layer.layer_id

    def remove_layer(self, layer_id):
        with self.lock:
            del self._layers[layer_id]  # Data survives other renderings and lineage.

    def remove_upload(self, dataset_id):
        with self.lock:
            self._uploads.remove(dataset_id)  # Detach upload; displayed data survives.

    def delete_dataset(self, dataset_id, *, cascade=False):
        with self.lock:
            if dataset_id not in self._datasets:
                raise KeyError(dataset_id)
            children = [k for k, d in self._datasets.items() if dataset_id in d.parent_ids]
            layers = [k for k, l in self._layers.items() if l.dataset_id == dataset_id]
            if (children or layers) and not cascade:
                raise ValueError("Dataset is still referenced")
            for key in children:
                if key in self._datasets:
                    self.delete_dataset(key, cascade=True)
            for key in layers:
                del self._layers[key]
            if dataset_id in self._uploads:
                self._uploads.remove(dataset_id)
            del self._datasets[dataset_id]

    def reset(self):
        with self.lock:
            self._datasets.clear()
            self._layers.clear()
            self._uploads.clear()
            self._results.clear()

    def replace_from_legacy(self, raw, shown):
        """Import an explicit old context into this session; retain no input refs."""
        replacement = SessionData()
        by_identity = {}
        for entry in raw:
            key = replacement.add_upload(entry["name"], entry["data"], file_id=entry.get("file_id"),
                                         renderer=entry.get("type", "geojson"), data_fields=entry.get("data_fields", ()))
            by_identity[id(entry["data"])] = key
        for entry in shown:
            key = by_identity.get(id(entry["data"]))
            if key is None:
                key = replacement.add_dataset(Dataset(entry["name"], entry["data"], source="legacy-display"))
                by_identity[id(entry["data"])] = key
            replacement.add_layer(Layer(key, entry["name"], entry.get("type", "geojson"),
                                        entry.get("data_field"), deepcopy(entry.get("folium_kwargs", {}))))
        with self.lock:
            self._datasets, self._layers, self._uploads = replacement._datasets, replacement._layers, replacement._uploads
            self._results = []

    @classmethod
    def from_legacy(cls, raw=(), shown=()):
        session = cls()
        session.replace_from_legacy(raw, shown)
        return session

    def resolve_in(self, pool, reference, *, require_layer=False):
        layer_matches = [e for e in pool if e.get("layer_id") == reference]
        if layer_matches:
            return layer_matches[0]
        if not require_layer:
            # Explicit dataset identity is independent of display visibility.
            # Name lookup below retains the legacy tool's pool selection.
            try:
                dataset = self.get_dataset(reference)
            except KeyError:
                pass
            else:
                return {"name": dataset.name, "type": dataset.metadata.get("type", "geojson"),
                        "data": self._payload(dataset), "dataset_id": dataset.dataset_id}
        matches = [e for e in pool if e.get("dataset_id") == reference]
        if matches and not require_layer:
            # Multiple renderings still refer to one dataset. Prefer its raw
            # projection when available; data operations do not choose a style.
            return next((e for e in matches if not e.get("layer_id")), matches[0])
        if not matches:
            matches = [e for e in pool if e["name"].lower() == reference.lower()]
        # A dataset plus its only same-named rendering denotes the same data;
        # multiple distinct renderings or same-named datasets are ambiguous.
        visible = [e for e in matches if e.get("layer_id")]
        if len(visible) == 1 and all(e["dataset_id"] == visible[0]["dataset_id"] for e in matches):
            return visible[0]
        unique = {(e.get("layer_id") or e.get("dataset_id")): e for e in matches}
        if len(unique) > 1:
            raise AmbiguousReference(f"Layer '{reference}' ist mehrdeutig. Bitte Dataset-ID oder Layer-ID verwenden.")
        return next(iter(unique.values()), None)

    def resolve(self, reference):
        with self.lock:
            raw, shown = self.legacy_layers()
            if reference in self._datasets:
                return self.get_dataset(reference), None
            match = self.resolve_in(shown + raw, reference)
            if match is None:
                raise KeyError(reference)
            return self.get_dataset(match["dataset_id"]), (
                self.get_layer(match["layer_id"]) if match.get("layer_id") else None)

    def record_failure(self, message, category="tool_error"):
        result = ToolResult(False, message, error_category=category)
        returned_result = deepcopy(result)
        with self.lock:
            self._results.append(result)
        return returned_result

    def commit_views(self, raw, shown, *, message, tool_name, parent_ids=(), metrics=None):
        """Validate detached tool output, then publish once under the session lock."""
        with self.lock:
            datasets, layers = dict(self._datasets), dict(self._layers)
            payload_ids = {id(e["data"]): e["dataset_id"] for e in raw + shown
                           if e.get("dataset_id") in datasets}
            created_data, created_layers, updated = [], [], []
            for entry in shown:
                if not isinstance(entry.get("name"), str) or not isinstance(entry.get("type"), str):
                    raise ValueError("Invalid layer contract")
                key = entry.get("dataset_id") or payload_ids.get(id(entry["data"]))
                if key is None:
                    data = entry["data"]
                    # Serialization validates transport compatibility, not GIS correctness.
                    if hasattr(data, "to_json") and hasattr(data, "geometry"):
                        json.loads(data.to_json())
                        crs = str(data.crs) if data.crs else None
                    else:
                        json.dumps(data)
                        crs = "EPSG:4326"
                    d = Dataset(entry["name"], deepcopy(data), crs, f"tool:{tool_name}", tuple(parent_ids),
                                {"format": entry.get("_format", "collection")})
                    key = d.dataset_id
                    datasets[key] = d
                    payload_ids[id(data)] = key
                    created_data.append(key)
                if key not in datasets:
                    raise ValueError("Unknown dataset reference")
                if entry.get("layer_id") and entry["layer_id"] not in self._layers:
                    raise ValueError("Unknown layer reference")
                layer_id = entry.get("layer_id") or new_id()
                if layer_id in self._layers and self._layers[layer_id].dataset_id != key:
                    raise ValueError("Existing layer cannot change dataset reference")
                candidate = Layer(key, entry["name"], entry["type"], entry.get("data_field"),
                                  deepcopy(entry.get("folium_kwargs", {})), layer_id)
                if layer_id not in layers:
                    created_layers.append(layer_id)
                elif candidate != layers[layer_id]:
                    updated.append(layer_id)
                layers[layer_id] = candidate
            for d in datasets.values():
                if any(key not in datasets for key in d.parent_ids):
                    raise ValueError("Invalid derived lineage")
            result = ToolResult(True, message, tuple(created_data), tuple(created_layers), tuple(updated), metrics or {})
            # All potentially failing work (including defensive copy) precedes publication.
            datasets = {key: self._copy_dataset(d) for key, d in datasets.items()}
            layers, result = deepcopy(layers), deepcopy(result)
            returned_result = deepcopy(result)
            self._datasets, self._layers = datasets, layers
            self._results.append(result)
            return returned_result
