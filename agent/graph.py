"""Single-agent ReAct workflow with explicit model, tools and layer access."""
from functools import partial
import math
from threading import RLock
from typing import TypedDict, Sequence, Annotated, Optional, List

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from core.session import SessionData
from .llm import create_llm


def _layer_attribute_summary(layer: dict) -> str:
    """Describe real property names and sampled value types for the agent."""
    data = layer.get("data")
    if isinstance(data, dict):
        if data.get("type") == "Feature":
            features = [data]
        else:
            features = data.get("features", [])
    elif isinstance(data, list):
        features = data
    else:
        features = []
    if not isinstance(features, list):
        features = []

    samples = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            continue
        for name, value in properties.items():
            field_samples = samples.setdefault(name, [])
            if len(field_samples) < 50:
                field_samples.append(value)

    summaries = []
    for name, values in samples.items():
        present = [value for value in values if value is not None]
        numeric = bool(present)
        saw_finite_number = False
        for value in present:
            if isinstance(value, bool):
                numeric = False
                break
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                numeric = False
                break
            if not math.isfinite(number):
                continue
            saw_finite_number = True
        numeric = numeric and saw_finite_number
        summaries.append(f"{name} ({'numerisch' if numeric else 'text/other'})")
    return ", ".join(summaries) if summaries else "Keine Attribute erkannt"

class AgentState(TypedDict):
    """LangGraph-Zustand: add_messages verwaltet den Nachrichtenverlauf der ReAct-Schleife."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    updated_show_layers: Optional[List[dict]]


def agent_reasoner(state: AgentState, *, llm_with_tools, get_layers) -> dict:
    """Reasoning-Phase: Das LLM entscheidet anhand des Kontexts, ob es antwortet oder ein Werkzeug aufruft."""
    raw_layers, show_layers = get_layers()
    raw_summary = [f"'{l['name']}' ({l['type']}; Attribute: {_layer_attribute_summary(l)})" + (
        f" [Dataset-ID: {l['dataset_id']}]" if l.get("dataset_id") else "") for l in raw_layers]
    show_summary = [f"'{l['name']}' ({l['type']}; Attribute: {_layer_attribute_summary(l)})" + (
        f" [Layer-ID: {l['layer_id']}, Dataset-ID: {l['dataset_id']}]" if l.get("layer_id") else "")
        for l in show_layers]

    system_prompt = f"""
    Du bist GeoGPT, ein autonomer und professioneller GIS-Agent.
    Deine Aufgabe ist es, Nutzerbefehle über räumliche Analysen und Kartenerstellung selbstständig auszuführen.

    AKTUELLER ZUSTAND DER ANWENDUNG:
    - Hochgeladene Rohdaten-Layer: [{', '.join(raw_summary) if raw_summary else 'Keine'}]
    - Derzeit auf der Karte angezeigte Layer: [{', '.join(show_summary) if show_summary else 'Keine'}]

    HANDLUNGSLEITFADEN:
    1. Du darfst und sollst Werkzeuge (Tools) verwenden, um Aktionen durchzuführen.
    2. Wenn ein Befehl mehrere Schritte erfordert (z.B. 'Lade Stadt Dresden und lege einen 1000m Puffer an'), führe die Werkzeuge nacheinander aus.
    3. Bei Choropleth-Karten darfst du nur ein tatsächlich aufgeführtes, numerisches Attribut verwenden; erfinde niemals einen Feldnamen aus dem Layer- oder Dateinamen.
    4. Wurde kein Choropleth-Attribut genannt und gibt es genau ein numerisches Attribut, verwende es und nenne es in der Antwort. Gibt es mehrere, frage nach einer Auswahl, außer der Nutzer hat die Auswahl ausdrücklich dir überlassen; dann wähle ein passendes Attribut und nenne es.
    5. Bei einem nicht verfügbaren oder nicht numerischen Attribut erkläre den Fehler und nenne die verfügbaren numerischen Attribute. Behaupte nur dann, eine Karte erstellt zu haben, wenn das Werkzeug erfolgreich war.
    6. Gib dem Nutzer nach Beendigung aller Tool-Aufrufe eine prägnante, informative Zusammenfassung auf Deutsch.
    """

    # Systemnachrichten entfernen und den aktuellen Systemkontext an den Anfang setzen.
    user_and_assistant_msgs = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
    full_messages = [SystemMessage(content=system_prompt)] + list(user_and_assistant_msgs)

    response = llm_with_tools.invoke(full_messages)
    return {"messages": [response]}


def should_continue(state: AgentState):
    """Bedingte Weiterleitung: Werkzeugaufruf ausführen oder die abschließende Antwort zurückgeben."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


def build_workflow(llm_with_tools, tools, get_layers):
    """Build the original graph with an already-bound model and explicit inputs."""
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", partial(agent_reasoner, llm_with_tools=llm_with_tools, get_layers=get_layers))
    node = ToolNode(tools)

    def one_call_state(state, call):
        messages = list(state["messages"])
        messages[-1] = messages[-1].model_copy(update={"tool_calls": [call]})
        return {**state, "messages": messages}

    def run_tools(state, config):
        messages = []
        for call in state["messages"][-1].tool_calls:
            messages.extend(node.invoke(one_call_state(state, call), config)["messages"])
        return {"messages": messages}

    async def run_tools_async(state, config):
        messages = []
        for call in state["messages"][-1].tool_calls:
            result = await node.ainvoke(one_call_state(state, call), config)
            messages.extend(result["messages"])
        return {"messages": messages}

    # ToolNode still executes/validates each tool, but a model batch is ordered
    # explicitly in sync AND async paths rather than relying on the prompt.
    workflow.add_node("tools", RunnableLambda(run_tools, afunc=run_tools_async))
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")
    return workflow


def create_agent_app(llm, tools, get_layers):
    """Bind tools exactly once and compile; caller supplies the model instance."""
    return build_workflow(llm.bind_tools(tools), tools, get_layers).compile()


class AgentRuntime:
    """Lazy execution runtime, with one model/binding/graph per instance.

    The initialization lock only protects construction. Session-bound tools own
    their data and enforce single-tool atomic publication separately.
    """

    def __init__(self, model_factory, tools, get_layers):
        self._model_factory = model_factory
        self._tools = tools
        self._get_layers = get_layers
        self._model = self._bound_model = self._workflow = self._app = None
        self._lock = RLock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                self._model = self._model_factory()
            return self._model

    @property
    def bound_model(self):
        with self._lock:
            if self._bound_model is None:
                self._bound_model = self.model.bind_tools(self._tools)
            return self._bound_model

    @property
    def workflow(self):
        with self._lock:
            if self._workflow is None:
                self._workflow = build_workflow(self.bound_model, self._tools, self._get_layers)
            return self._workflow

    @property
    def app(self):
        with self._lock:
            if self._app is None:
                self._app = self.workflow.compile()
            return self._app

    def invoke(self, *args, **kwargs):
        return self.app.invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        return await self.app.ainvoke(*args, **kwargs)


class SessionAgent:
    """Agent execution and seven tools explicitly bound to one business session."""

    def __init__(self, session: SessionData, model_factory=None):
        from .tools import create_tools

        self.session = session
        self.TOOLS = create_tools(session)
        for registered in self.TOOLS:
            setattr(self, registered.name, registered)
        self._runtime = AgentRuntime(model_factory or create_llm, self.TOOLS, self.get_agent_layers)
        self.agent_app = self._runtime

    def get_agent_layers(self):
        return self.session.get_agent_layers()

    @property
    def llm(self):
        return self._runtime.model

    @property
    def llm_with_tools(self):
        return self._runtime.bound_model

    @property
    def workflow(self):
        return self._runtime.workflow


def create_agent_session(session: SessionData | None = None, model_factory=None) -> SessionAgent:
    """Create a private agent; an omitted session creates a fresh container."""
    return SessionAgent(session if session is not None else SessionData(), model_factory)
