"""Lazy DeepSeek configuration and optional upload metadata advice.

Importing this module neither reads credentials nor constructs a model.
"""
import json
import os
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage


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
