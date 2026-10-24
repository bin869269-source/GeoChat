"""Lazy DeepSeek configuration and optional upload metadata advice.

Importing this module neither reads credentials nor constructs a model.
"""
import json
import math
import os
from pathlib import Path

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage


def serialize_metadata_sample(data) -> str:
    """Encode the existing upload preview without changing its source data.

    Keep three list entries, two FeatureCollection features, or the existing
    1000-character fallback. NumPy numeric/bool scalars become Python scalars;
    missing/non-finite floating values become null only in this AI preview.
    Unsupported objects remain errors rather than being stringified. The
    fallback may still truncate JSON, matching the existing sampling policy.
    """
    def compatible(value):
        if isinstance(value, (np.integer, np.floating, np.bool_)):
            value = value.item()
            if isinstance(value, np.generic):
                raise TypeError("Extended-precision metadata scalar cannot be encoded losslessly")
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: compatible(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [compatible(item) for item in value]
        return value

    truncate = False
    if isinstance(data, list):
        sample = data[:3]
    elif isinstance(data, dict) and "features" in data:
        sample = {"type": "FeatureCollection", "features": data.get("features", [])[:2]}
    else:
        sample = data
        truncate = True
    text = json.dumps(compatible(sample), ensure_ascii=False, allow_nan=False)
    return text[:1000] if truncate else text


def create_llm(api_key=None):
    """Create a client on demand, retaining the existing configuration priority.

    Explicit key, or repository .env/environment, then Streamlit secrets. No
    credential value is logged. Streamlit is only consulted during creation.
    """
    if api_key is None:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            try:
                import streamlit as st

                api_key = st.secrets.get("DEEPSEEK_API_KEY")
            except Exception:
                pass
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY wurde nicht gefunden!")

    # Import here so importing agent.llm never constructs an API client.
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model="deepseek-chat",
        openai_api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout=60,
        max_retries=2,
    )


def smart_analyze_geodata(filename: str, sample_data_str: str, *, llm=None) -> dict:
    system_prompt = """
    Analysiere diesen Geodaten-Auszug und antworte NUR mit JSON:
    {
      "detected_type": "points" | "line" | "polygon" | "geojson",
      "data_fields": ["feld1", "feld2"],
      "recommended_layer_name": "Ein schöner deutscher Name"
    }
    """
    try:
        if llm is None:
            llm = create_llm()
        resp = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=f"Datei: {filename}\nAuszug:\n{sample_data_str}")])
        import re
        match = re.search(r'(\{.*\})', resp.content.strip(), re.DOTALL)
        return json.loads(match.group(1)) if match else {"detected_type": "geojson", "data_fields": [], "recommended_layer_name": filename}
    except Exception:
        return {"detected_type": "geojson", "data_fields": [], "recommended_layer_name": filename}
