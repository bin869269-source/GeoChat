"""Explicit Chrome/CDP acceptance, using installed Chrome and websockets only.

Run: .venv\\Scripts\\python.exe tests/phase4a_acceptance.py
All artifacts, temporary browser profiles and synthetic uploads go to ignored
tests/logs/phase4a-<uuid>/. No production file or real credential is modified.
The browser operates the real frontend; only LLM/OSM/credential access is isolated.
"""
from dataclasses import asdict
import base64
from collections import Counter
import json
import os
import re
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request
import urllib.parse
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_installed = False
TOOL_NAMES = (
    "fetch_city_boundary_osm", "visualize_layer_on_map", "calculate_buffer_analysis",
    "calculate_area_and_density", "run_machine_learning_spatial_prediction",
    "update_map_style", "calculate_feature_distances",
)


def classify_network_failures(events):
    """Separate proven, already-loaded duplicate tile cancellations only.

    Chrome can report an Other request for a PNG just loaded as an Image in
    the same Folium frame, then cancel that duplicate immediately. Require
    positive delivery evidence and tight timing; all other failures stay fatal.
    """
    frames, requests, responses, finished = {}, {}, {}, {}
    expected, unexpected = [], []
    for event in events:
        method, params = event["method"], event["params"]
        session = event.get("sessionId")
        key = (session, params.get("requestId"))
        if method == "Page.frameNavigated":
            value = params["frame"]
            frames[(session, value["id"])] = value.get("url", "")
        elif method == "Network.requestWillBeSent":
            requests[key] = {**params, "frame_url": frames.get((session, params.get("frameId")), "")}
        elif method == "Network.responseReceived":
            responses[key] = params
        elif method == "Network.loadingFinished":
            finished[key] = params
        elif method == "Network.loadingFailed":
            request = requests.get(key, {})
            url = request.get("request", {}).get("url", "")
            start, end = request.get("timestamp"), params.get("timestamp")
            frame_url = urllib.parse.urlsplit(request.get("frame_url", ""))
            duplicate = (params.get("canceled") is True and params.get("errorText") == "net::ERR_ABORTED"
                and params.get("type") == "Other" and re.fullmatch(r"https://tile\.openstreetmap\.org/\d+/\d+/\d+\.png", url)
                and frame_url.hostname in ("127.0.0.1", "::1", "localhost")
                and frame_url.path == "/component/streamlit_folium.st_folium/index.html"
                and start is not None and end is not None and 0 <= end-start <= .25
                and key not in responses)
            delivered = []
            if duplicate:
                for prior_key, complete in finished.items():
                    prior, response = requests.get(prior_key, {}), responses.get(prior_key, {}).get("response", {})
                    if (prior_key != key and prior_key[0] == session and prior.get("frameId") == request.get("frameId")
                        and prior.get("type") == "Image" and prior.get("request", {}).get("url") == url
                        and response.get("status") == 200 and response.get("mimeType") == "image/png"
                        and 0 <= start-complete.get("timestamp", float("inf")) <= .25):
                        delivered.append(prior_key[1])
            if delivered:
                expected.append({"failure": event, "url": url, "delivered_request_ids": delivered,
                                 "reason": "Already-loaded same-frame PNG; immediate duplicate Other request canceled"})
            else:
                unexpected.append(event)
    return expected, unexpected


class AcceptanceModel:
    """Scripted responses, not a language model or a substitute GIS engine."""
    def __init__(self):
        self.sequence = 0
        self.metadata_samples = []

    def bind_tools(self, tools):
        assert tuple(t.name for t in tools) == TOOL_NAMES
        return SimpleNamespace(invoke=self.reason)

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        filename = messages[-1].content.split("Datei: ", 1)[1].splitlines()[0]
        def invalid_constant(token):
            raise ValueError("Non-standard metadata JSON constant: " + token)
        sample = json.loads(messages[-1].content.split("Auszug:\n", 1)[1], parse_constant=invalid_constant)
        self.metadata_samples.append({"filename": filename, "sample": sample})
        return AIMessage(content=json.dumps({
            "detected_type": "points" if filename.endswith((".csv", ".json")) else "geojson",
            "data_fields": ["value"], "recommended_layer_name": Path(filename).stem}))

    def reason(self, messages):
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        command = json.loads(messages[start].content.removeprefix("ACCEPT "))
        feedback = [m.content for m in messages[start + 1:] if isinstance(m, ToolMessage)]
        steps = command.get("steps", [])
        if steps and not feedback:
            calls = []
            for step in steps:
                self.sequence += 1
                calls.append({**step, "id": str(self.sequence), "type": "tool_call"})
            return AIMessage(content="", tool_calls=calls)
        return AIMessage(content="ACCEPT DONE " + command["nonce"] + "\n" + "\n".join(feedback))


def run_acceptance_app():
    """Keep production parsing/tools/graph/session/rendering/UI paths intact."""
    global _installed
    import streamlit as st
    if not _installed:
        import dotenv
        import httpx
        import requests
        from agent import graph

        os.environ.pop("DEEPSEEK_API_KEY", None)
        dotenv.load_dotenv = lambda *a, **k: False  # Never open a real .env.
        st.secrets = {}  # Never consult real user secrets.

        def blocked(*args, **kwargs):
            raise AssertionError("Acceptance forbids external API/network access")

        requests.sessions.Session.request = blocked
        httpx.Client.send = httpx.AsyncClient.send = blocked
        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

        def local_only(original):
            def connect(sock, address):
                if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                    return original(sock, address)
                return blocked()
            return connect

        socket.socket.connect = local_only(original_connect)
        socket.socket.connect_ex = local_only(original_connect_ex)
        if os.environ["PHASE4A_MODE"] == "mock":
            graph.create_llm = AcceptanceModel
            from agent import tools
            boundary = json.loads((Path(os.environ["PHASE4A_OUT"]) / "fixtures/regions.geojson").read_text())

            def osm(url, **kwargs):
                assert url.startswith("https://nominatim.openstreetmap.org/search?")
                return SimpleNamespace(status_code=200, json=lambda: boundary)

            tools.requests.get = osm
        _installed = True
    try:
        runpy.run_path(str(ROOT / "frontend.py"), run_name="__main__")
    finally:
        if "business_session" in st.session_state:
            session = st.session_state.business_session
            data = {"session_id": session.session_id,
                    "datasets": [{"id": d.dataset_id, "name": d.name, "metadata": d.metadata,
                                  "crs": d.crs, "frame": hasattr(d.data, "geometry"),
                                  "first_geometry": (d.data.geometry.iloc[0].__geo_interface__
                                      if hasattr(d.data, "geometry") and len(d.data) else
                                      d.data["features"][0]["geometry"]
                                      if isinstance(d.data, dict) and d.data.get("features") else None)}
                                 for d in session.datasets.values()],
                    "layers": [asdict(x) for x in session.layers.values()],
                    "results": [asdict(x) for x in session.results],
                    "messages": st.session_state.messages,
                    "metadata_samples": getattr(st.session_state.session_agent._runtime._model, "metadata_samples", []),
                    "tools": [t.name for t in st.session_state.session_agent.TOOLS],
                    "raw_count": len(session.raw_view)}
            dest = Path(os.environ["PHASE4A_OUT"]) / "snapshots" / (session.session_id + ".json")
            dest.parent.mkdir(exist_ok=True)
            temp = dest.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temp.replace(dest)


def wait_for(fn, timeout=25):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (OSError, ValueError, KeyError) as exc:
            last = exc
        time.sleep(.15)
    raise TimeoutError(f"Acceptance wait timed out: {last}")


class CDP:
    def __init__(self, url):
        from websockets.sync.client import connect
        self.ws = connect(url, proxy=None, open_timeout=5, max_size=32 * 1024 * 1024)
        self.sequence = 0
        self.events = []

    def call(self, method, params=None, session=None):
        self.sequence += 1
        packet = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            packet["sessionId"] = session
        self.ws.send(json.dumps(packet))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = json.loads(self.ws.recv(timeout=max(.1, deadline - time.monotonic())))
            if response.get("id") == packet["id"]:
                if "error" in response:
                    raise RuntimeError(str(response["error"]))
                return response.get("result", {})
            self.events.append(response)
        raise TimeoutError(method)

    def js(self, session, expression):
        result = self.call("Runtime.evaluate", {"expression": expression,
                           "returnByValue": True, "userGesture": True}, session)
        if "exceptionDetails" in result:
            raise RuntimeError(str(result["exceptionDetails"]))
        return result.get("result", {}).get("value")


def make_fixtures(out):
    directory = out / "fixtures"
    directory.mkdir()
    def write(name, value):
        (directory/name).write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    def feature(geometry, value):
        return {"type": "Feature", "geometry": geometry, "properties": {"name": str(value), "value": value}}
    def square(x, y):
        return [[x,y],[x+.015,y],[x+.015,y+.015],[x,y+.015],[x,y]]
    write("points_csv.csv", "lat,lon,name,value\n51.001,9.001,A,3\n51.003,9.003,B,7\n51.006,9.006,C,11\n")
    write("points_json.json", [{"lat":51.002,"lon":9.002,"name":"JSON-A","properties":{"value":2}},
                               {"lat":51.004,"lon":9.004,"name":"JSON-B","properties":{"value":6}}])
    write("regions.geojson", {"type":"FeatureCollection", "features":[
        feature({"type":"Polygon","coordinates":[square(9,51)]}, 3),
        feature({"type":"Polygon","coordinates":[square(9.02,51)]}, 8)]})
    write("lines.geojson", {"type":"FeatureCollection", "features":[feature({
        "type":"LineString", "coordinates":[[9,51],[9.01,51.01],[9.03,51.01]]},4)]})
    write("multi.geojson", {"type":"FeatureCollection", "features":[feature({
        "type":"MultiPolygon", "coordinates":[[square(9,51)],[square(9.02,51)]]},5)]})
    write("numeric.csv", "lat,lon,value\n51,9,3\n")
    write("utm_points.geojson", {"type": "FeatureCollection",
          "crs": {"type": "name", "properties": {"name": "EPSG:25832"}},
          "features": [feature({"type": "Point", "coordinates": [500000 + i*100, 5649824.888]}, i + 1)
                       for i in range(3)]})
    write("bad.csv", "latitude,longitude\n51,9\n")
    write("bad.json", "{broken")
    return directory


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    out = ROOT / "tests/logs" / ("phase4a-" + uuid4().hex)
    out.mkdir(parents=True)
    fixtures = make_fixtures(out)
    cases, children, handles, browser_pages = [], [], [], []
    cdp = None
    def record(name, status, detail):
        cases.append({"id": name, "status": status, "detail": detail})
        print(status, name, detail, flush=True)
        (out/"results.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    def check(name, fn):
        try:
            detail = fn()
            record(name, "Passed", str(detail or "assertions satisfied"))
            return True
        except Exception as exc:
            record(name, "Failed", str(exc))
            import traceback
            (out/(name+"-failure.txt")).write_text(traceback.format_exc(),encoding="utf-8")
            for index, session in enumerate(browser_pages):
                try:
                    screenshot(session,name+f"-failure-{index}")
                except Exception:
                    pass
            return False
    def start(command, log_name, env=None):
        log = (out/log_name).open("w", encoding="utf-8")
        handles.append(log)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        children.append(process)
        return process
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get_json(url):
        return json.loads(opener.open(url, timeout=2).read())
    def server(mode):
        env = {key:os.environ[key] for key in ("SYSTEMROOT","WINDIR","PATH","TEMP","TMP",
               "USERPROFILE","HOMEDRIVE","HOMEPATH","APPDATA","LOCALAPPDATA") if key in os.environ}
        env.update(PHASE4A_MODE=mode, PHASE4A_OUT=str(out), PYTHONIOENCODING="utf-8",
                   PROJ_NETWORK="OFF", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
        process = start([sys.executable,"-m","streamlit","run","tests/acceptance_app.py",
                         "--server.address","127.0.0.1","--server.port","0","--server.headless","true",
                         "--server.fileWatcherType","none","--browser.gatherUsageStats","false",
                         "--logger.hideWelcomeMessage","false"], mode+"-server.log", env)
        from tests.check_startup import _bound_address
        def ready():
            assert process.poll() is None, "Acceptance server exited"
            base = _bound_address((out/(mode+"-server.log")).read_text(encoding="utf-8",errors="replace"))
            if base and opener.open(base+"/_stcore/health",timeout=1).read().strip()==b"ok":
                return base
        return process, wait_for(ready)
    def snapshot(session_id):
        return json.loads((out/"snapshots"/(session_id+".json")).read_text(encoding="utf-8"))
    def body(session):
        return cdp.js(session,"document.body?.innerText || ''") or ""
    def screenshot(session, name):
        image = cdp.call("Page.captureScreenshot", {"format":"png","captureBeyondViewport":True}, session)
        (out/(name+".png")).write_bytes(base64.b64decode(image["data"]))
    def page(url):
        previous = set((out/"snapshots").glob("*.json")) if (out/"snapshots").exists() else set()
        context = cdp.call("Target.createBrowserContext")["browserContextId"]
        cdp.call("Browser.setDownloadBehavior", {"behavior":"allow", "browserContextId":context,
                 "downloadPath":str(out/"downloads"), "eventsEnabled":True})
        target = cdp.call("Target.createTarget", {"url":"about:blank","browserContextId":context})["targetId"]
        session = cdp.call("Target.attachToTarget", {"targetId":target,"flatten":True})["sessionId"]
        browser_pages.append(session)
        for method in ["Page.enable","Runtime.enable","Log.enable","Network.enable"]:
            cdp.call(method, session=session)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width":1440,"height":1100,
                 "deviceScaleFactor":1,"mobile":False}, session)
        cdp.call("Page.navigate", {"url":url}, session)
        wait_for(lambda:"Geodaten hochladen" in body(session), 40)
        new = wait_for(lambda:list(set((out/"snapshots").glob("*.json"))-previous))
        assert len(new)==1
        return session, new[0].stem
    def upload(session, names):
        wait_for(lambda:cdp.js(session,"(()=>{const e=document.querySelector('input[type=file]');return !!e && !e.disabled})()"))
        doc = cdp.call("DOM.getDocument",{},session)["root"]["nodeId"]
        node = cdp.call("DOM.querySelector",{"nodeId":doc,"selector":"input[type=file]"},session)["nodeId"]
        assert node, "Uploader input not found"
        cdp.call("DOM.setFileInputFiles",{"nodeId":node,"files":[str(fixtures/n) for n in names]},session)
    def click(session, label):
        # During a Streamlit rerun a real button can exist but be disabled.
        # Wait for an actionable element, then send real browser mouse input.
        expression="(()=>{const e=[...document.querySelectorAll('button')].find(e=>e.innerText.trim()==="+json.dumps(label)+");if(!e||e.disabled)return null;e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
        point=wait_for(lambda:cdp.js(session,expression))
        for kind in ["mousePressed","mouseReleased"]:
            cdp.call("Input.dispatchMouseEvent",{"type":kind,"button":"left","clickCount":1,**point},session)
    def chat(session, steps):
        nonce = uuid4().hex[:8]
        command = "ACCEPT "+json.dumps({"steps":steps,"nonce":nonce})
        stable_since = None
        def ready():
            nonlocal stable_since
            idle = cdp.js(session,"""(()=>{const app=document.querySelector('[data-testid=stApp]');
              const e=document.querySelector('textarea[data-testid=stChatInputTextArea]');
              return !!e&&!e.disabled&&app?.getAttribute('data-test-script-state')==='notRunning'})()""")
            if not idle:
                stable_since = None
                return False
            stable_since = stable_since or time.monotonic()
            return time.monotonic()-stable_since >= 1
        wait_for(ready)
        assert cdp.js(session,"(()=>{const e=document.querySelector('textarea[data-testid=stChatInputTextArea]');if(!e||e.disabled)return false;e.focus();return document.activeElement===e})()")
        cdp.call("Input.insertText",{"text":command},session)
        assert cdp.js(session,"document.querySelector('textarea[data-testid=stChatInputTextArea]').value")==command
        for kind in ["keyDown","keyUp"]:
            cdp.call("Input.dispatchKeyEvent",{"type":kind,"key":"Enter","code":"Enter",
                     "windowsVirtualKeyCode":13},session)
        wait_for(lambda:"ACCEPT DONE "+nonce in body(session),40)
    def step(name, **args):
        return {"name":name,"args":args}
    def frames(session):
        return cdp.js(session,"""[...document.querySelectorAll('iframe')].map(f=>{try{
          const d=f.contentDocument;return {src:f.src,leaflet:d?.querySelectorAll('.leaflet-container').length,
          markers:d?.querySelectorAll('.leaflet-marker-pane > *').length,paths:d?.querySelectorAll('.leaflet-overlay-pane path').length,
          heat:d?.querySelectorAll('.leaflet-heatmap-layer').length,controls:d?.querySelectorAll('.leaflet-control-layers').length,
          text:d?.body?.innerText};}catch(e){return {src:f.src,error:String(e)}}})""")
    try:
        chrome = Path(os.environ["PROGRAMFILES"])/"Google/Chrome/Application/chrome.exe"
        profile = out/"profile"
        browser = start([str(chrome),"--headless=new","--remote-debugging-port=0",
                         "--remote-debugging-address=127.0.0.1","--user-data-dir="+str(profile),
                         "--no-first-run","--no-default-browser-check","--disable-default-apps",
                         "--disable-background-networking","--disable-sync","--disable-gpu",
                         "--in-process-gpu","about:blank"],"chrome.log")
        def browser_ready():
            assert browser.poll() is None, "Chrome exited"
            if (profile/"DevToolsActivePort").exists():
                port=(profile/"DevToolsActivePort").read_text().splitlines()[0]
                return get_json("http://127.0.0.1:"+port+"/json/version")
        info=wait_for(browser_ready)
        cdp=CDP(info["webSocketDebuggerUrl"])
        cdp.call("Browser.setDownloadBehavior",{"behavior":"allow","downloadPath":str(out/"downloads"),"eventsEnabled":True})
        record("ENV-BROWSER","Passed",info["Browser"]+"; isolated headless Chrome/CDP, real JavaScript")
        no_key_process, no_key_base=server("no-key")
        n, nid=page(no_key_base)
        assert cdp.js(n,"!!document.querySelector('textarea[data-testid=stChatInputTextArea]') && !!document.querySelector('iframe')")
        screenshot(n,"01-no-key-initial")
        record("UI-START","Passed","Real browser shows uploader/chat/map; no credentials inherited")
        # The production missing-key error is used; only credential sources are isolated.
        cdp.js(n,"document.querySelector('textarea').focus()")
        cdp.call("Input.insertText",{"text":"hello without a key"},n)
        for kind in ["keyDown","keyUp"]:
            cdp.call("Input.dispatchKeyEvent",{"type":kind,"key":"Enter","code":"Enter","windowsVirtualKeyCode":13},n)
        wait_for(lambda:"DEEPSEEK_API_KEY wurde nicht gefunden" in body(n))
        screenshot(n,"02-no-key-feedback")
        record("UI-NO-KEY-CHAT","Passed","Caught missing-key message displayed, not an unhandled Python exception")
        upload(n,["points_csv.csv"])
        wait_for(lambda:"Fehler beim Parsen" in body(n))
        assert snapshot(nid)["datasets"]==[]
        record("UI-NO-KEY-UPLOAD","Passed","Visible missing-key feedback; upload creates no dataset (model metadata is mandatory today)")
        mock_process, mock_base=server("mock")
        a, aid=page(mock_base)
        b, bid=page(mock_base)
        assert aid!=bid
        record("SESSION-CONTEXTS","Passed","Two distinct Chrome BrowserContexts and Streamlit business session IDs")
        upload(a,["points_csv.csv","points_json.json","regions.geojson","lines.geojson","multi.geojson"])
        state=wait_for(lambda:(s if len((s:=snapshot(aid))["datasets"])==5 else None))
        assert {x["name"] for x in state["datasets"]}=={"points_csv","points_json","regions","lines","multi"}
        assert all(x["metadata"]["data_fields"]==["value"] for x in state["datasets"])
        assert state["layers"]==[] and "Erfolgreich geladen" in body(a)
        record("UPLOAD-FORMATS","Passed","Actual browser file upload CSV/JSON/GeoJSON -> 5 datasets; mock metadata names/type/fields; no implicit displayed layers")
        show=[step("visualize_layer_on_map",layer_name=name,map_type=kind) for name,kind in [
              ("points_csv","points"),("points_json","points"),("regions","geojson"),("lines","geojson"),("multi","geojson")]]
        chat(a,show)
        state=wait_for(lambda:(s if len((s:=snapshot(aid))["layers"])==5 else None))
        assert all(r["success"] for r in state["results"])
        record("AGENT-SHOW-GEOMETRIES","Passed","Real LangGraph/ToolNode dispatched five ordered calls; point/line/polygon/multipolygon datasets rendered")
        def geometry_ready():
            found=frames(a)
            return found if any(x.get("leaflet") and x.get("paths",0)>=4 and x.get("markers",0)>=5 for x in found) else None
        initial_frames=wait_for(geometry_ready)
        (out/"initial-map-dom.json").write_text(json.dumps(initial_frames,ensure_ascii=False,indent=2),encoding="utf-8")
        screenshot(a,"03-geometry-map")
        record("MAP-BROWSER-GEOMETRIES","Passed","Leaflet iframe has >=5 real marker nodes and >=4 SVG paths; screenshot 03")
        chat(a,[step("visualize_layer_on_map",layer_name="points_csv",map_type="heatmap",data_field="value"),
                step("visualize_layer_on_map",layer_name="regions",map_type="choropleth",data_field="value")])
        wait_for(lambda:any(f.get("heat",0)>0 and f.get("controls",0)>0 for f in frames(a)))
        assert any("regions_choropleth - value" in (f.get("text") or "") for f in frames(a))
        screenshot(a,"04-heatmap-choropleth")
        record("MAP-THEMATIC","Passed","Real browser Leaflet heat canvas and layer control; choropleth created from value field")
        chat(a,[step("calculate_buffer_analysis",layer_name="points_csv_points",distance_meters=50),
                step("update_map_style",layer_name="Puffer_50m_points_csv_points",color="red",weight=4,fill_opacity=.5),
                step("calculate_area_and_density",polygon_layer_name="regions",point_layer_name="points_csv"),
                step("calculate_feature_distances",layer_name="points_csv",max_pairs=3),
                step("run_machine_learning_spatial_prediction",layer_name="points_csv",target_field="value"),
                step("fetch_city_boundary_osm",city_name="Acceptance")])
        state=wait_for(lambda:(s if len((s:=snapshot(aid))["results"])>=13 else None))
        assert all(r["success"] for r in state["results"]), state["results"]
        names={x["name"] for x in state["layers"]}
        assert {"Puffer_50m_points_csv_points","Dichte_regions","ML_Vorhersage_points_csv","Stadtgrenze_Acceptance"} <= names
        assert any(x["frame"] for x in state["datasets"]), "GIS output should retain its GeoDataFrame"
        assert tuple(state["tools"])==TOOL_NAMES and len({x["dataset_id"] for x in state["layers"]})<len(state["layers"])
        record("AGENT-ALL-SEVEN","Passed","All seven tools executed via browser chat and real Graph; real GIS and seeded RandomForest; LLM/OSM mocked")
        record("AGENT-MULTIROUND","Passed","Three browser chat turns, ordered dependent buffer/style steps and shared dataset references")
        screenshot(a,"05-analysis-map")
        def red_paths():
            return cdp.js(a,"""[...document.querySelectorAll('iframe')].some(f=>{try{return [...f.contentDocument.querySelectorAll('.leaflet-overlay-pane path')].some(p=>p.getAttribute('stroke')==='red' && p.getAttribute('stroke-width')==='4')}catch(e){return false}})""")
        wait_for(red_paths)
        record("MAP-BROWSER-STYLE","Passed","Buffer SVG path actually has red stroke and width 4 in browser")
        # LayerControl toggles only display; canonical business state is unchanged.
        toggle="""(()=>{for(const f of document.querySelectorAll('iframe')){try{
          const d=f.contentDocument;const label=[...d.querySelectorAll('.leaflet-control-layers-overlays label')].find(e=>e.innerText.trim()==='points_csv_points');
          if(label){const box=label.querySelector('input');const before=box.checked;box.click();return {before,after:box.checked};}
        }catch(e){}}return null})()"""
        toggled=cdp.js(a,toggle)
        assert toggled=={"before":True,"after":False}, toggled
        assert snapshot(aid)["layers"]==state["layers"]
        assert cdp.js(a,toggle)=={"before":False,"after":True}
        record("MAP-LAYER-CONTROL","Passed","Real Leaflet checkbox hides and restores point layer without altering canonical Layer records")
        before=state
        chat(a,[step("calculate_buffer_analysis",layer_name="absent",distance_meters=50)])
        state=wait_for(lambda:(s if (s:=snapshot(aid))["results"] and not s["results"][-1]["success"] else None))
        assert state["datasets"]==before["datasets"] and state["layers"]==before["layers"]
        assert "nicht gefunden" in body(a)
        record("AGENT-FAILURE","Passed","Missing layer feedback reaches browser, with no partial dataset/layer publication")
        # Real downloaded artifact, not just a captured download_button argument.
        def download_map():
            click(a,"Karte als HTML exportieren")
            wait_for(lambda:"HTML-Datei herunterladen" in body(a))
            click(a,"📥 HTML-Datei herunterladen")
            download=out/"downloads/geogpt_map.html"
            def completed_download():
                cdp.js(a,"document.readyState")  # Drain actual Chrome download/network events.
                begins=[e["params"] for e in cdp.events if e.get("method")=="Browser.downloadWillBegin"
                        and e["params"].get("suggestedFilename")=="geogpt_map.html"]
                return bool(begins and any(e.get("method")=="Browser.downloadProgress"
                    and e["params"].get("guid")==begins[-1]["guid"]
                    and e["params"].get("state")=="completed" for e in cdp.events)
                    and download.exists() and download.stat().st_size>1000
                    and not list((out/"downloads").glob("*.crdownload")))
            wait_for(completed_download)
            responses=[e["params"]["response"] for e in cdp.events if e.get("method")=="Network.responseReceived"]
            assert any(r.get("status")==200 and r.get("mimeType")=="text/html"
                       and 'filename="geogpt_map.html"' in {k.lower():v for k,v in r.get("headers",{}).items()}.get("content-disposition", "")
                       for r in responses), "Real HTML download response not observed"
            html=download.read_bytes()
            assert b"L.marker(" in html and b"L.geoJson(" in html and b"L.heatLayer(" in html
            assert b'"color": "red"' in html and not (ROOT/"map_export.html").exists()
            return f"geogpt_map.html downloaded by Chrome; {len(html)} bytes; points/geometry/heat/style present; no shared export file"
        check("EXPORT-BROWSER-DOWNLOAD",download_map)
        # The second independent session uploads and executes before resetting A.
        upload(b,["points_json.json"])
        wait_for(lambda:len(snapshot(bid)["datasets"])==1)
        chat(b,[step("visualize_layer_on_map",layer_name="points_json",map_type="points")])
        other=wait_for(lambda:(s if len((s:=snapshot(bid))["layers"])==1 else None))
        assert set(x["id"] for x in other["datasets"]).isdisjoint(x["id"] for x in state["datasets"])
        record("SESSION-UPLOAD-TOOLS","Passed","Separate BrowserContexts uploaded independently; tools changed only their own session; dataset IDs disjoint")
        # Deletion uses the actual sidebar button and retains canonical datasets.
        assert cdp.js(a,"""(()=>{const e=[...document.querySelectorAll('[data-testid=stExpander]')].find(e=>e.innerText.includes('Aktive Kartenlayer'));const b=[...e.querySelectorAll('button')].find(e=>e.innerText.trim()==='✕');if(!b)return false;b.click();return true})()""")
        wait_for(lambda:len(snapshot(aid)["layers"])==len(state["layers"])-1)
        assert snapshot(aid)["datasets"]==state["datasets"]
        record("UI-DELETE-LAYER","Passed","Sidebar deletion removes one Layer, retaining Dataset; other session untouched")
        click(a,"🗑️ Chat & Sitzung zurücksetzen")
        wait_for(lambda:not snapshot(aid)["datasets"] and not snapshot(aid)["layers"])
        assert snapshot(bid)==other
        assert not snapshot(aid)["messages"] and snapshot(aid)["raw_count"]==0
        wait_for(lambda:"Hallo! Ich bin dein autonomer GIS-Agent." in body(a) and "points_csv.csv" not in body(a))
        record("SESSION-RESET","Passed","Reset A clears uploads/layers/history and does not change B's canonical state")
        upload(a,["bad.csv","bad.json"])
        wait_for(lambda:"CSV muss die Spalten" in body(a) and "Fehler beim Parsen von 'bad.json'" in body(a))
        assert not snapshot(aid)["datasets"]
        record("UPLOAD-ERRORS","Passed","Malformed JSON and missing CSV lat/lon give visible errors; no dataset created")
        click(a,"🗑️ Chat & Sitzung zurücksetzen")
        wait_for(lambda:"bad.csv" not in body(a))
        # Streamlit can rerun after an upload and remove its one-run success
        # alert before the next CDP poll. Observe the real DOM before uploading;
        # retain the same success assertion without requiring it to persist.
        cdp.js(a,"""(()=>{
          window.__numericUploadUI={success:false,errors:[],messages:[]};
          window.__numericUploadObserver=new MutationObserver(()=>{
            for(const node of document.querySelectorAll('[data-testid=stAlert]')){
              const text=node.innerText;
              if(text.includes('Erfolgreich geladen: numeric (points)')){
                window.__numericUploadUI.success=true;
                if(!window.__numericUploadUI.messages.includes(text))window.__numericUploadUI.messages.push(text);
              }
              if(text.includes("Fehler beim Parsen von 'numeric.csv'")){
                if(!window.__numericUploadUI.errors.includes(text))window.__numericUploadUI.errors.push(text);
              }
            }
          });
          window.__numericUploadObserver.observe(document.body,{subtree:true,childList:true,characterData:true});
        })()""")
        upload(a,["numeric.csv"])
        numeric_state=wait_for(lambda:(s if len((s:=snapshot(aid))["datasets"])==1 else None))
        wait_for(lambda:cdp.js(a,"window.__numericUploadUI.success"))
        upload_ui=cdp.js(a,"window.__numericUploadUI")
        assert upload_ui["success"] and upload_ui["messages"] and upload_ui["errors"]==[]
        (out/"numeric-upload-ui.json").write_text(json.dumps(upload_ui,ensure_ascii=False,indent=2),encoding="utf-8")
        cdp.js(a,"window.__numericUploadObserver.disconnect()")
        assert "not JSON serializable" not in body(a) and "Fehler beim Parsen" not in body(a)
        assert numeric_state["datasets"][0]["name"]=="numeric"
        assert numeric_state["datasets"][0]["metadata"]["data_fields"]==["value"]
        assert numeric_state["layers"]==[] and numeric_state["results"]==[]
        assert numeric_state["metadata_samples"][-1]["filename"]=="numeric.csv"
        sample=numeric_state["metadata_samples"][-1]["sample"]
        assert sample==[{"name":"Point 1","lat":51.0,"lon":9.0,"properties":{"value":3}}]
        assert type(sample[0]["properties"]["value"]) is int
        assert snapshot(bid)==other
        screenshot(a,"06-numeric-csv-fixed")
        record("KD-CSV-METADATA","Passed","Former defect fixed: real numeric CSV upload produces integer metadata and registers one Dataset; no new Layer/result or cross-session change")
        # Title is a renderer API; the production page has no editable title widget.
        from maps.renderer import create_layered_map, Titel_hinzufügen, render_map_html
        title_map=create_layered_map([{"name":"title_points","type":"points","data":[{"lat":51,"lon":9,"name":"title point"}]}],show_basemap=False)
        Titel_hinzufügen(title_map,"Phase 4A title acceptance")
        title_file=out/"title-map.html"
        title_file.write_bytes(render_map_html(title_map))
        target=cdp.call("Target.createTarget",{"url":"about:blank"})["targetId"]
        title_session=cdp.call("Target.attachToTarget",{"targetId":target,"flatten":True})["sessionId"]
        for method in ["Page.enable","Runtime.enable","Log.enable","Network.enable"]:
            cdp.call(method,session=title_session)
        cdp.call("Page.navigate",{"url":title_file.as_uri()},title_session)
        wait_for(lambda:"Phase 4A title acceptance" in body(title_session))
        def title_ready():
            return cdp.js(title_session,"!!document.querySelector('.leaflet-container') && document.querySelectorAll('.leaflet-marker-pane > *').length===1")
        if check("MAP-TITLE-STANDALONE-BROWSER",lambda:wait_for(title_ready)):
            screenshot(title_session,"07-standalone-title")
        record("MAP-TITLE-UI-EDIT","Not Run","Production page has no title input/tool; only the existing renderer API is exercised, no test-only title UI added")
        assert all(cdp.js(s,"document.querySelectorAll('[data-testid=stException]').length")==0 for s in [a,b,n])
        record("UI-PYTHON-EXCEPTIONS","Passed","No uncaught Python exception element in any of the three actual browser pages")
        # Preserve all runtime/console/network evidence for review.
        cdp.js(a,"document.body.innerText")
        errors=[e for e in cdp.events if e.get("method") in ("Runtime.exceptionThrown","Log.entryAdded","Network.loadingFailed")]
        (out/"browser-events.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
        exceptions=[e for e in errors if e.get("method")=="Runtime.exceptionThrown"]
        console_errors=[e for e in errors if e.get("method")=="Log.entryAdded" and e["params"]["entry"].get("level")=="error"]
        expected_cancellations, network_failures=classify_network_failures(cdp.events)
        warning_counts=Counter((e["params"]["entry"].get("source"),e["params"]["entry"]["text"])
            for e in errors if e.get("method")=="Log.entryAdded" and e["params"]["entry"].get("level")=="warning")
        (out/"browser-diagnostics.json").write_text(json.dumps({
            "runtime_exceptions":len(exceptions),"console_errors":len(console_errors),
            "network_failures":len(network_failures),
            "expected_duplicate_tile_cancellations":expected_cancellations,
            "warnings":[{"source":source,"text":text,"count":count} for (source,text),count in warning_counts.items()]
        },ensure_ascii=False,indent=2),encoding="utf-8")
        record("BROWSER-EXCEPTIONS","Failed" if exceptions or console_errors or network_failures else "Passed",
               f"{len(exceptions)} uncaught exceptions, {len(console_errors)} console errors, {len(network_failures)} network failures; warnings preserved separately")
        record("REAL-EXTERNAL-SERVICES","Not Run","No real DeepSeek API or live Nominatim request; acceptance responses are explicitly scripted")
    except Exception as exc:
        record("ACCEPTANCE-RUNNER","Failed",type(exc).__name__+": "+str(exc))
        import traceback
        (out/"failure.txt").write_text(traceback.format_exc(),encoding="utf-8")
        if cdp:
            for label in ["a","b","n"]:
                session=locals().get(label)
                if session:
                    try:
                        (out/(label+"-failure-dom.txt")).write_text(body(session),encoding="utf-8")
                        screenshot(session,label+"-failure")
                        (out/(label+"-frames.json")).write_text(json.dumps(frames(session),ensure_ascii=False,indent=2),encoding="utf-8")
                    except Exception:
                        pass
    finally:
        cleanup_errors=[]
        if cdp:
            try:
                (out/"cdp-events.json").write_text(json.dumps(cdp.events,ensure_ascii=False),encoding="utf-8")
            except Exception as exc:
                cleanup_errors.append("CDP evidence: "+str(exc))
            try:
                cdp.call("Browser.close")
            except Exception:
                pass  # Owned Popen handles are still reaped below.
            try:
                cdp.ws.close()
            except Exception as exc:
                cleanup_errors.append("CDP socket: "+str(exc))
        from tests.check_startup import _stop_process, _wait_for_port_close
        for process in reversed(children):
            try:
                if process is locals().get("browser"):
                    try:
                        process.wait(timeout=5)  # Allow Browser.close to reap Chrome children first.
                    except subprocess.TimeoutExpired:
                        pass
                _stop_process(process)
            except Exception as exc:
                cleanup_errors.append(f"Owned PID {process.pid}: {exc}")
        ports=[]
        for key in ["no_key_base","mock_base"]:
            if key in locals():
                base=locals()[key]
                try:
                    _wait_for_port_close(base)
                    ports.append({"service":key,"address":base,"closed":True})
                except Exception as exc:
                    ports.append({"service":key,"address":base,"closed":False})
                    cleanup_errors.append(str(exc))
        profile_port=out/"profile/DevToolsActivePort"
        if profile_port.exists():
            base="http://127.0.0.1:"+profile_port.read_text().splitlines()[0]
            try:
                _wait_for_port_close(base)
                ports.append({"service":"Chrome CDP","address":base,"closed":True})
            except Exception as exc:
                ports.append({"service":"Chrome CDP","address":base,"closed":False})
                cleanup_errors.append(str(exc))
        for log in handles:
            try:
                log.close()
            except Exception as exc:
                cleanup_errors.append(str(exc))
        (out/"cleanup-check.json").write_text(json.dumps({"ports":ports,
            "children":[{"pid":p.pid,"exit_code":p.poll()} for p in children],
            "errors":cleanup_errors},indent=2),encoding="utf-8")
        record("CLEANUP","Failed" if cleanup_errors else "Passed",
               "; ".join(cleanup_errors) if cleanup_errors else "Owned Streamlit/Chrome processes reaped; Streamlit and Chrome CDP ports closed")
        print("ARTIFACTS:",out.relative_to(ROOT).as_posix(),flush=True)
    return int(any(case["status"]=="Failed" for case in cases))


if __name__ == "__main__":
    raise SystemExit(main())
