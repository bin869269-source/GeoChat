"""Focused real-Chrome CRS acceptance; only the existing test LLM/OSM are mocked.

Run with the project interpreter. Evidence stays in ignored tests/logs. Uses
the same real frontend entrypoint, uploader, Graph, GIS and Renderer as Phase4A.
"""
from collections import Counter
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.phase4a_acceptance import CDP, make_fixtures, wait_for
from tests.check_startup import _bound_address, _stop_process, _wait_for_port_close


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    out = ROOT / "tests/logs" / ("phase4d-browser-" + uuid4().hex)
    out.mkdir(parents=True)
    fixtures = make_fixtures(out)
    children, handles, cases = [], [], []
    cdp = None
    base = chrome_base = None
    def record(name, status, detail):
        cases.append({"id": name, "status": status, "detail": detail})
        (out/"results.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        print(status, name, detail, flush=True)
    def start(command, name, env=None):
        handle = (out/name).open("w", encoding="utf-8")
        handles.append(handle)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=handle,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        children.append(process)
        return process
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def snapshot(identity):
        return json.loads((out/"snapshots"/(identity+".json")).read_text(encoding="utf-8"))
    def page():
        before = set((out/"snapshots").glob("*.json")) if (out/"snapshots").exists() else set()
        context = cdp.call("Target.createBrowserContext")["browserContextId"]
        cdp.call("Browser.setDownloadBehavior", {"behavior": "allow", "browserContextId": context,
                 "downloadPath": str(out/"downloads"), "eventsEnabled": True})
        target = cdp.call("Target.createTarget", {"url": "about:blank", "browserContextId": context})["targetId"]
        session = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        for method in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            cdp.call(method, session=session)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1100,
                 "deviceScaleFactor": 1, "mobile": False}, session)
        cdp.call("Page.navigate", {"url": base}, session)
        wait_for(lambda: cdp.js(session, "!!document.querySelector('input[type=file]')"), 40)
        new = wait_for(lambda: list(set((out/"snapshots").glob("*.json"))-before))
        assert len(new) == 1
        # Operate the existing checkbox; overlays do not require online tiles.
        wait_for(lambda: cdp.js(session, """(()=>{const label=[...document.querySelectorAll('label')].find(e=>e.innerText.includes('OpenStreetMap Basemap anzeigen'));
          const input=label?.querySelector('input[type=checkbox]');if(!input||input.disabled)return false;if(input.checked)input.click();return true})()"""))
        return session, new[0].stem
    def upload(session, name):
        wait_for(lambda: cdp.js(session, "!!document.querySelector('input[type=file]:not(:disabled)')"))
        document = cdp.call("DOM.getDocument", session=session)["root"]["nodeId"]
        node = cdp.call("DOM.querySelector", {"nodeId": document, "selector": "input[type=file]"}, session)["nodeId"]
        cdp.call("DOM.setFileInputFiles", {"nodeId": node, "files": [str(fixtures/name)]}, session)
    def click(session, text):
        expression = "(()=>{const e=[...document.querySelectorAll('button')].find(e=>e.innerText.trim()==="+json.dumps(text)+");if(!e||e.disabled)return false;e.click();return true})()"
        wait_for(lambda: cdp.js(session, expression))
    try:
        env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP",
               "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA") if k in os.environ}
        env.update(PHASE4A_MODE="mock", PHASE4A_OUT=str(out), PYTHONIOENCODING="utf-8",
                   PROJ_NETWORK="OFF", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
        service = start([sys.executable, "-m", "streamlit", "run", "tests/acceptance_app.py",
                         "--server.address", "127.0.0.1", "--server.port", "0", "--server.headless", "true",
                         "--server.fileWatcherType", "none", "--browser.gatherUsageStats", "false"], "server.log", env)
        def server_ready():
            assert service.poll() is None, "Streamlit exited"
            address = _bound_address((out/"server.log").read_text(encoding="utf-8", errors="replace"))
            return address if address and opener.open(address+"/_stcore/health", timeout=1).read().strip()==b"ok" else None
        base = wait_for(server_ready)
        profile = out/"profile"
        browser = start([str(Path(os.environ["PROGRAMFILES"])/"Google/Chrome/Application/chrome.exe"),
                         "--headless=new", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                         "--user-data-dir="+str(profile), "--no-first-run", "--no-default-browser-check",
                         "--disable-background-networking", "--disable-sync", "--disable-gpu", "--in-process-gpu",
                         "about:blank"], "chrome.log")
        def browser_ready():
            assert browser.poll() is None, "Chrome exited"
            if (profile/"DevToolsActivePort").exists():
                port = (profile/"DevToolsActivePort").read_text().splitlines()[0]
                return "http://127.0.0.1:"+port
        chrome_base = wait_for(browser_ready)
        info = json.loads(opener.open(chrome_base+"/json/version", timeout=2).read())
        cdp = CDP(info["webSocketDebuggerUrl"])
        a, aid = page()
        b, bid = page()
        assert aid != bid
        upload(a, "utm_points.geojson")
        upload(b, "points_json.json")
        source_state = wait_for(lambda: (s if len((s:=snapshot(aid))["datasets"])==1 else None))
        other = wait_for(lambda: (s if len((s:=snapshot(bid))["datasets"])==1 else None))
        source = source_state["datasets"][0]
        assert source["crs"] == "EPSG:25832" and source["first_geometry"]["coordinates"] == [500000,5649824.888]
        assert not source_state["layers"] and set(d["id"] for d in other["datasets"]).isdisjoint([source["id"]])
        record("UPLOAD-CRS-SESSIONS", "Passed", info["Browser"]+"; real uploader, independent BrowserContexts; UTM coordinates and CRS preserved")
        steps = [{"name": name, "args": arguments} for name, arguments in (
            ("visualize_layer_on_map", {"layer_name": source["id"], "map_type": "points"}),
            ("calculate_feature_distances", {"layer_name": source["id"]}),
            ("calculate_buffer_analysis", {"layer_name": source["id"], "distance_meters": 50}),
            ("calculate_area_and_density", {"polygon_layer_name": "Puffer_50m_utm_points"}))]
        nonce = uuid4().hex
        command = "ACCEPT "+json.dumps({"steps": steps, "nonce": nonce})
        wait_for(lambda: cdp.js(a, """(()=>{const e=document.querySelector('textarea[data-testid=stChatInputTextArea]');if(!e||e.disabled)return false;e.focus();return document.activeElement===e})()"""))
        cdp.call("Input.insertText", {"text": command}, a)
        assert cdp.js(a, "document.querySelector('textarea').value") == command
        for kind in ("keyDown", "keyUp"):
            cdp.call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, a)
        wait_for(lambda: "ACCEPT DONE "+nonce in cdp.js(a, "document.body.innerText"), 40)
        state = snapshot(aid)
        assert len(state["results"]) == 4 and all(r["success"] for r in state["results"])
        assert next(r for r in state["results"] if "min_distance_m" in r["metrics"])["metrics"]["min_distance_m"] == 100
        assert next(d for d in state["datasets"] if d["id"] == source["id"]) == source
        assert len(state["datasets"]) == 3 and all(d["crs"] == "EPSG:4326" for d in state["datasets"] if d["frame"])
        assert snapshot(bid) == other
        record("GRAPH-CRS-ANALYSIS", "Passed", "Real chat -> LangGraph/ToolNode; 100m; buffer then derived area; no source or other-session mutation")
        cdp.js(a, "document.querySelector('iframe').scrollIntoView({block:'center'})")
        def map_positions():
            result = cdp.js(a, """(()=>{for(const f of document.querySelectorAll('iframe')){try{
              const m=f.contentWindow.map;if(!m?.eachLayer)continue;const c=m.getContainer();if(!c.isConnected)continue;
              const r=[];let inBounds=true;m.eachLayer(l=>{if(typeof l.getLatLng==='function'){const p=l.getLatLng();r.push([p.lat,p.lng]);inBounds&&=m.getBounds().contains(p);}});
              return {positions:r,inBounds,markers:c.querySelectorAll('.leaflet-marker-pane > *').length,paths:c.querySelectorAll('.leaflet-overlay-pane path').length};
            }catch(e){}}return null})()""")
            (out/"map-dom-last.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result if result and len(result["positions"])==3 and result["markers"]==3 and result["paths"]>=6 and result["inBounds"] else None
        rendered = wait_for(map_positions)
        positions = rendered["positions"]
        assert abs(positions[0][0]-51)<1e-5 and abs(positions[0][1]-9)<1e-5
        assert all(50<p[0]<52 and 8<p[1]<10 for p in positions)
        (out/"marker-positions.json").write_text(json.dumps(positions, indent=2), encoding="utf-8")
        (out/"map-dom.json").write_text(json.dumps(rendered, indent=2), encoding="utf-8")
        shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, a)
        (out/"crs-map.png").write_bytes(base64.b64decode(shot["data"]))
        record("MAP-WGS84", "Passed", "Three actual Leaflet markers near (51,9), in map bounds; >=6 SVG paths; real DOM, JavaScript and screenshot")
        cdp.js(a, """(()=>{window.__crsDownloaded=false;window.__crsObserver=new MutationObserver(()=>{
          const e=[...document.querySelectorAll('button')].find(e=>e.innerText.trim()==='📥 HTML-Datei herunterladen'&&!e.disabled);
          if(e&&!window.__crsDownloaded){window.__crsDownloaded=true;e.click();window.__crsObserver.disconnect();}
        });window.__crsObserver.observe(document.body,{subtree:true,childList:true,characterData:true});})()""")
        click(a, "Karte als HTML exportieren")
        def downloaded():
            cdp.js(a, "document.readyState")
            begun={e["params"]["guid"] for e in cdp.events if e["method"]=="Browser.downloadWillBegin" and e["params"]["suggestedFilename"]=="geogpt_map.html"}
            done=[e["params"] for e in cdp.events if e["method"]=="Browser.downloadProgress" and e["params"].get("guid") in begun and e["params"].get("state")=="completed"]
            file=out/"downloads/geogpt_map.html"
            return file if done and file.exists() and file.stat().st_size>1000 else None
        exported=wait_for(downloaded)
        html=exported.read_text(encoding="utf-8")
        positions_html=[json.loads(value) for value in re.findall(r"L\.marker\(\s*(\[[^\]]+\])", html)]
        assert len(positions_html)==3 and abs(positions_html[0][0]-51)<1e-5 and abs(positions_html[0][1]-9)<1e-5
        assert "L.geoJson(" in html and not (ROOT/"map_export.html").exists()
        responses=[e["params"]["response"] for e in cdp.events if e["method"]=="Network.responseReceived"]
        assert any(r["status"]==200 and r.get("mimeType")=="text/html" and 'filename="geogpt_map.html"' in
                   {k.lower():v for k,v in r.get("headers",{}).items()}.get("content-disposition", "") for r in responses)
        record("HTML-DOWNLOAD", "Passed", f"Actual page download, Chrome completion event, MIME/name and WGS84 coordinates; {exported.stat().st_size} bytes")
        click(a, "🗑️ Chat & Sitzung zurücksetzen")
        wait_for(lambda: not snapshot(aid)["datasets"] and not snapshot(aid)["layers"])
        assert snapshot(bid)==other
        record("RESET-ISOLATION", "Passed", "Reset A clears only A; B dataset/CRS unchanged")
        assert all(cdp.js(s, "document.querySelectorAll('[data-testid=stException]').length")==0 for s in (a,b))
        errors=[e for e in cdp.events if e["method"]=="Runtime.exceptionThrown" or
                e["method"]=="Log.entryAdded" and e["params"]["entry"].get("level")=="error"]
        assert not errors
        record("BROWSER-ERRORS", "Passed", "No Python exception element, uncaught JavaScript exception or console error; network events preserved separately")
    except Exception as exc:
        import traceback
        (out/"failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        record("CRS-RUNNER", "Failed", type(exc).__name__+": "+str(exc))
    finally:
        errors=[]
        if cdp:
            try:
                (out/"browser-events.json").write_text(json.dumps(cdp.events, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                errors.append("Evidence: "+str(exc))
            try:
                cdp.call("Browser.close")
            except Exception:
                pass
            try:
                cdp.ws.close()
            except Exception as exc:
                errors.append("CDP close: "+str(exc))
        for process in reversed(children):
            try:
                _stop_process(process)
            except Exception as exc:
                errors.append(str(exc))
        for address in (base, chrome_base):
            if address:
                try:
                    _wait_for_port_close(address)
                except Exception as exc:
                    errors.append(str(exc))
        for handle in handles:
            handle.close()
        (out/"cleanup.json").write_text(json.dumps({"children": [{"pid":p.pid,"exit_code":p.poll()} for p in children], "errors": errors}, indent=2), encoding="utf-8")
        record("CLEANUP", "Failed" if errors else "Passed", "; ".join(errors) if errors else "Owned Chrome/Streamlit reaped and both ports closed")
        print("ARTIFACTS:", out.relative_to(ROOT).as_posix())
        print("RESULTS:", dict(Counter(c["status"] for c in cases)))
    return int(any(c["status"]=="Failed" for c in cases))


if __name__ == "__main__":
    raise SystemExit(main())
