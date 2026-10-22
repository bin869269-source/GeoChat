# GeoChat

Streamlit GIS 应用，使用 DeepSeek 与单 Agent LangGraph 完成数据可视化和空间分析。
正式工作区为 GeoChat-Sync；核心代码采用 agent/、gis/、maps/、core/，不再需要 backend.py。

## 环境与依赖

已验证 Windows x64 / CPython 3.14.5；其他 Python 和系统组合尚未验证。
复用现有 tests/requirements.txt（应用及测试直接依赖）与 tests/constraints.txt：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r tests/requirements.txt
```

已有 .venv 无须重复安装。现有环境由 uv 创建，若没有 pip，可使用已有 uv：

```powershell
uv pip install --python .venv\Scripts\python.exe -r tests/requirements.txt
```

安装需要包源访问权限；安装后的核心测试不需要互联网或真实 API Key。

## 配置与启动

空白页面和纯 GIS/Maps/Core 操作无需模型 Key。实际对话及上传时的 AI 元数据分析需要配置：

```powershell
$env:DEEPSEEK_API_KEY = "<your-key>"
.venv\Scripts\python.exe -m streamlit run frontend.py
```

也支持仓库根目录本地 .env，或环境变量缺失时回退 Streamlit secrets。
.env 已被忽略；不要提交真实 Key 或 secrets 文件。
保持 deepseek-chat、https://api.deepseek.com、60 秒超时、最多 2 次重试。
模块导入和 Session Agent 创建不初始化模型，实际模型访问时才读取配置。

仅检查本机无凭据启动：

```powershell
.venv\Scripts\python.exe -m streamlit run frontend.py --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

Folium/Leaflet 的浏览器脚本、样式和底图可能需要外部资源；服务端离线 HTML 不等于浏览器离线地图。

## 模块与正式入口

| 模块 | 职责与入口 |
|---|---|
| frontend.py | Streamlit 上传、聊天、图层管理和 HTML 下载 |
| agent/graph.py | create_agent_session、SessionAgent、延迟运行时与 ReAct Graph |
| agent/tools.py | create_tools(session)、七工具及名称/属性清理辅助 |
| agent/llm.py | create_llm、smart_analyze_geodata；配置和元数据建议 |
| gis/data_io.py | parse_uploaded_file、extract_features_to_gdf、属性清理 |
| gis/geometry.py | Buffer、几何最短距离 |
| gis/statistics.py | 面积、区域点计数和密度 |
| maps/renderer.py | Folium 图层、标题、HeatMap/Choropleth、render_map_html |
| core/contracts.py | Dataset、Layer、ToolResult |
| core/session.py | SessionData、稳定 ID、引用生命周期与单工具提交 |
| tests/ | 回归、独立 GIS 参考、架构与服务端集成测试 |

```python
from agent.graph import create_agent_session
from core.session import SessionData
from gis.data_io import parse_uploaded_file
from maps.renderer import create_layered_map, render_map_html

session = SessionData()
agent = create_agent_session(session)
# parsed = parse_uploaded_file(file_like_with_name)
# key = session.add_upload("source", parsed["data"], renderer=parsed["type"])
# agent.visualize_layer_on_map.invoke({"layer_name": key})
html_bytes = render_map_html(create_layered_map(session.map_view))
# 对话：agent.agent_app.invoke({"messages": [...]})
# 异步：await agent.agent_app.ainvoke({"messages": [...]})
```

每用户持有独立 SessionData 与 SessionAgent；省略 session 时工厂每次新建容器。
Dataset 持有数据，Layer 只保存引用与显示配置。重名使用 Dataset/Layer ID 消歧。
删除 Layer 保留数据；删除 Dataset 默认保护引用，仅显式 cascade 才递归删除依赖。
Sidebar 删除上传项只是取消上传列表归属；Reset 清空当前会话业务状态。

七工具保持：fetch_city_boundary_osm、visualize_layer_on_map、calculate_buffer_analysis、
calculate_area_and_density、run_machine_learning_spatial_prediction、update_map_style、calculate_feature_distances。
现有随机森林保留，GNN 训练属于独立项目。

## 旧入口迁移

backend.py 已移除，未知外部脚本没有自动兼容保证：

- SessionAgent、create_agent_session、Graph 入口从 agent.graph 导入。
- 模型及元数据分析从 agent.llm 导入；复用模型时传 llm=agent.llm。
- 数据解析、空间运算、地图辅助分别从 gis/maps 的对应模块导入。
- 旧 set_agent_context 改用 session.replace_from_legacy(raw, shown)，或
  SessionData.from_legacy(raw, shown) 后传给工厂；显式整体替换会重新分配导入对象 ID。
- 不再提供 backend 动态属性/静态重导出、全局 agent_app、TOOLS 或默认用户状态。
- 工具从显式 Agent 的 TOOLS/同名属性取得，或使用 agent.tools.create_tools(session)。

## 测试与限制

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
# 显式本机启动与 WebSocket 页面检查（自动停止服务）：
.venv\Scripts\python.exe tests/check_startup.py
```

测试隔离真实凭据并阻止外部网络，使用脚本化模型与 Mock Nominatim，真实执行 GIS、Graph、
随机森林与 Folium。strict xfail 表示 Known Defect，不算 Passed。
当前 13 项历史缺陷继续跟踪，包括 GIS 精度/输入、地图输入及纯数值 CSV 元数据采样问题；
KD-SESSION 已修复，隔离测试正常通过。
详见 [测试说明](tests/README.md) 和 [Phase 3E 报告](tests/PHASE3E_REPORT.md)。
AppTest 不执行浏览器 JavaScript；真实模型、线上 OSM、浏览器视觉和生产负载不在自动化通过声明内。
