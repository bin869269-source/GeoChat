"""Explicit local Streamlit smoke check; not part of offline pytest collection.

Starts only an owned loopback server, uses no inherited API credentials, requests
the real page over WebSocket, and always stops the process. No browser JS runs.
"""
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from websockets.sync.client import connect


ROOT = Path(__file__).resolve().parents[1]


def _bound_address(server_log):
    # Streamlit prints its URL only after server.start() has bound the socket.
    match = re.search(r"URL: http://127\.0\.0\.1:(\d+)", server_log)
    if match and 0 < int(match.group(1)) <= 65535:
        return f"http://127.0.0.1:{match.group(1)}"
    return None


def _stop_process(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait_for_port_close(base):
    # Windows may release the listening socket shortly after process.wait().
    deadline = time.monotonic() + 2
    while True:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", int(base.rsplit(":", 1)[1]))) != 0:
                return
        if time.monotonic() >= deadline:
            raise RuntimeError("Startup port still listening after process cleanup")
        time.sleep(0.05)


def main():
    # Keep Windows profile lookup working without inheriting API keys, proxy
    # credentials or tracing credentials. Do not change HOME/USERPROFILE.
    environment = {key: os.environ[key] for key in (
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE",
        "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA") if key in os.environ}
    environment.update(STREAMLIT_BROWSER_GATHER_USAGE_STATS="false", LANGSMITH_TRACING="false",
                       LANGCHAIN_TRACING_V2="false", PROJ_NETWORK="OFF", PYTHONIOENCODING="utf-8")
    assert "DEEPSEEK_API_KEY" not in environment
    command = [sys.executable, "-m", "streamlit", "run", "frontend.py",
               "--server.address", "127.0.0.1", "--server.port", "0",
               "--server.headless", "true", "--browser.gatherUsageStats", "false",
               "--server.fileWatcherType", "none", "--logger.hideWelcomeMessage", "false"]
    log_path = ROOT / f"tests/logs/phase3e-startup-server-{uuid4().hex}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("x", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        base = None
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, "Streamlit exited; inspect local server log"
                base = _bound_address(log_path.read_text(encoding="utf-8", errors="replace"))
                if base:
                    try:
                        with opener.open(base + "/_stcore/health", timeout=1) as response:
                            assert response.status == 200 and response.read().strip() == b"ok"
                        assert process.poll() is None, "Owned Streamlit process exited during readiness"
                        break
                    except (OSError, TimeoutError):
                        pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Local startup timed out")
                time.sleep(0.15)
            print(f"PASS real Streamlit health HTTP 200 at {base}; no inherited DeepSeek Key")
            with opener.open(base, timeout=5) as response:
                assert response.status == 200 and "streamlit" in response.read().decode("utf-8").lower()
            print("PASS root HTML bootstrap HTTP 200")
            elements, headings, exceptions = set(), [], []
            with connect(base.replace("http://", "ws://", 1) + "/_stcore/stream",
                         subprotocols=["streamlit", "phase3e-smoke"], proxy=None,
                         open_timeout=5, close_timeout=2, max_size=16 * 1024 * 1024) as websocket:
                message = BackMsg()
                message.rerun_script.SetInParent()
                websocket.send(message.SerializeToString())
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    frame = websocket.recv(timeout=max(0.1, deadline - time.monotonic()))
                    forward = ForwardMsg()
                    forward.ParseFromString(frame)
                    kind = forward.WhichOneof("type")
                    if kind == "delta" and forward.delta.WhichOneof("type") == "new_element":
                        element = forward.delta.new_element
                        element_type = element.WhichOneof("type")
                        elements.add(element_type)
                        if element_type == "markdown":
                            headings.append(element.markdown.body)
                        if element_type == "exception":
                            exceptions.append(element.exception.message)
                    if kind == "script_finished":
                        assert forward.script_finished == ForwardMsg.FINISHED_SUCCESSFULLY
                        break
                else:
                    raise RuntimeError("Page script did not finish")
            assert not exceptions, exceptions
            assert {"file_uploader", "chat_input", "component_instance"} <= elements, elements
            assert any("GeoGPT" in heading for heading in headings)
            assert process.poll() is None, "Owned Streamlit process exited during page execution"
            print("PASS real WebSocket page: uploader/chat/map received, script finished, no exceptions")
            print("NOT RUN browser JavaScript/visual map, real DeepSeek and Nominatim")
        finally:
            _stop_process(process)
            if base:
                _wait_for_port_close(base)
            print("Owned Streamlit process stopped; log:", log_path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
