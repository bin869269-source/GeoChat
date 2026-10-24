# GeoChat — Phase 4A 应用端到端验收

验收日期：2026-10-24（Europe/Berlin）。首次实施未修改业务源码、既有测试、依赖或配置，未修复历史缺陷，未 Commit、Push 或 Merge。下列 Part I–X 保留首次实施记录；随后 Finalization 的审核、脚本最小加强及本地提交范围见文末。

结论：主要应用链路在真实 Chrome 和可控模型响应下通过。完整 pytest 为 **208 Passed、13 strict xfail**；独立浏览器验收记录为 **23 Passed、1 Known Defect、2 Not Run、0 Failed、0 Blocked**。这些验收记录不是新增的 pytest 用例。浏览器中的 1 项 Known Defect 是原有 KD-CSV-METADATA，不能与原有 13 项相加。

真实 DeepSeek、在线 Nominatim、自由自然语言理解和人工交互体验验收尚未执行，不能将本结论描述成真实外部服务全部通过。

## Part I — Environment & Git Baseline

| 检查 | 状态 | 实际结果 |
|---|---|---|
| 正式根目录 | Passed | 当前 GeoChat-Sync 工作区；`git rev-parse --show-toplevel` 与工作目录一致 |
| Branch | Passed | `refactor/modular-architecture` |
| HEAD | Passed | `5330368ddb633c3bff8edc5d916d60dc1a692cf8` |
| main | Passed | `dcad948d029842a555c609149b5cc226a1fdfe90`，验收前后未变 |
| 初始工作区 | Passed | `git status --short` 无输出，`backend.py` 不存在 |
| Python | Passed | 现有 `.venv/Scripts/python.exe`，CPython 3.14.5、Windows x64 |
| 浏览器 | Passed | 已安装 Chrome 156.0.8078.47，CDP 1.3；使用临时 profile 和独立 BrowserContexts |
| 浏览器驱动环境 | Passed | 未安装 Playwright/Selenium；利用已有 websockets 16.1.1 连接 Chrome CDP，没有安装依赖 |

实际依赖：pytest 9.1.1、Streamlit 1.66.1、GeoPandas 1.2.0、Folium 0.20.0、Branca 0.8.2、LangGraph 1.2.16、langchain-core 1.6.13、langchain-openai 1.7.2、NumPy 2.5.4、pandas 3.0.6、Shapely 2.2.0、pyproj 3.8.0、scikit-learn 1.9.1。

业务入口和职责检查：`frontend.py` 直接调用 `agent.graph.create_agent_session`、`agent.llm.smart_analyze_geodata`、`gis.data_io.parse_uploaded_file`、`maps.renderer`。`SessionData` 管理 Dataset/Layer、引用生命周期、锁和原子发布。未恢复旧 backend 入口。

## Part II — Streamlit Startup

| 检查 | 状态 | 验证层级和结果 |
|---|---|---|
| 正式入口启动 | Passed | 执行已有 `tests/check_startup.py`，实际运行 `streamlit run frontend.py` |
| HTTP | Passed | `/_stcore/health` 返回 200/ok，根页面返回 200 |
| WebSocket | Passed | 请求真实页面执行，收到上传、聊天、地图组件，`script_finished` 成功，无 Python 异常 |
| 真实浏览器组件 | Passed | Chrome 实际加载 Streamlit、执行 JavaScript，上传/聊天/地图区域显示，见截图 01/03/04 |
| 无 Key 聊天 | Passed | 实际输入聊天文本，显示 `DEEPSEEK_API_KEY wurde nicht gefunden!` 的捕获错误反馈，无未处理 Python 异常 |
| 无 Key 上传 | Passed | 使用正常 CSV，页面显示缺 Key 的上传错误，不发布 Dataset；当前上传元数据阶段需要模型，见 Part VIII |
| 服务退出 | Passed | 每个自有子进程执行 wait/terminate/kill 清理；Streamlit 和 Chrome 调试端口均已关闭 |

README 正式启动命令仍为 `.venv\Scripts\python.exe -m streamlit run frontend.py`。浏览器验收另用 `tests/acceptance_app.py`，这是测试包装入口，不能作为正式启动入口。

包装入口通过 `runpy` 执行原始 `frontend.py`，只隔离凭据来源、外部网络和模型/OSM 响应。没有替换上传控件、解析器、Graph、ToolNode、工具实现、GIS、随机森林、Session 或 Renderer。无 Key 模式保留真实模型工厂的缺 Key 检查，禁止读取真实 `.env` 或 Streamlit secrets。

## Part III — Upload and Data Processing

生成的小型数据均位于证据目录 `fixtures/`，脚本每次确定性生成：

| 数据/操作 | 状态 | 实际结果 |
|---|---|---|
| `points_csv.csv` | Passed | 3 点，lat/lon/name/value；实际文件控件上传并创建 Dataset |
| `points_json.json` | Passed | 2 点，lat/lon/properties.value；实际上传并创建 Dataset |
| `regions.geojson` | Passed | 2 个 Polygon，具有不同 value；创建 Dataset |
| `lines.geojson` | Passed | 1 个 LineString；创建 Dataset |
| `multi.geojson` | Passed | 1 个双部件 MultiPolygon；创建 Dataset |
| 字段和元数据 | Passed | Mock 仅建议名称、类型和 value 字段；真实解析结果入库，Dataset.metadata 保留 data_fields |
| Dataset → Layer | Passed | 上传后仅有 5 个 Dataset、0 个显示 Layer；通过真实 Agent 工具创建显示 Layer；多个 Layer 引用同一 Dataset |
| GeoDataFrame | Passed | Buffer、面积/密度、随机森林实际执行并发布 GeoDataFrame 派生数据，未以 Mock 替换计算 |
| `bad.csv` | Passed | 缺少 lat/lon，页面报告所需字段，不发布 Dataset |
| `bad.json` | Passed | 非法 JSON，页面报告解析失败，不发布 Dataset |
| `numeric.csv` | Known Defect | `lat,lon,value\n51,9,3\n` 在元数据采样时出现 int64 JSON 序列化错误，见 KD-CSV-METADATA |
| UI 元数据展示范围 | Passed | Sidebar 实际显示名称和类型；字段列表只在 Dataset.metadata 中保存，当前页面未单独展示字段详情 |

错误路径是在真实浏览器中执行，不是仅调用解析函数。空/无效几何、坐标范围及 CRS 等边界由原有 pytest 覆盖，不能将其全部算成浏览器上传场景已验收。

## Part IV — Map Visualization

| 功能 | 状态 | 证据和范围 |
|---|---|---|
| Point | Passed | 实际 Leaflet marker pane 中存在 5 个点标记，截图 03 |
| LineString / Polygon / MultiPolygon | Passed | 三类原始数据均经上传和工具创建图层；实际 iframe 有 4 个 SVG path，截图 03；图层名称在控制器中保留 |
| HeatMap | Passed | 真实 Leaflet 热力 canvas 创建，截图 04 可见热点 |
| Choropleth | Passed | value 字段实际生成色带、图例和多边形着色；截图 04 的 `regions_choropleth - value` 图例可见 |
| 样式更新 | Passed | 多步分析创建 Buffer 后更新样式；真实 SVG stroke=red、stroke-width=4，配置 fill_opacity=0.5 |
| 图层开关 | Passed | 实际 Leaflet checkbox 隐藏并恢复点图层，Session 中 Layer 记录不变 |
| 图层顺序/默认地图参数 | Passed | 原有自动化回归继续通过；本阶段没有修改 bounds、中心或顺序策略 |
| Sidebar 删除 Layer | Passed | 点击真实删除按钮，Layer 数量减少 1，Dataset 全部保留 |
| Reset | Passed | 真实按钮清空当前会话上传、Dataset、Layer、结果和聊天；另一会话不受影响 |
| 标题 Renderer + 浏览器 | Passed | 使用正式标题 API 生成 standalone HTML，Chrome 实际执行 Leaflet 并显示标题和 1 个点，截图 07 |
| 从页面编辑地图标题 | Not Run | 页面当前没有标题输入控件，七工具也没有标题参数；没有为验收添加测试专用产品 UI |

截图 03/04/07 已实际查看。该验证覆盖本次合成数据和 Chrome headless 场景，不等于完成各种设备/浏览器的人工视觉验收。HeatMap 的矢量样式参数是否具有用户预期的视觉效果，未单独作浏览器验收。

## Part V — Agent Execution

| 工具 | 状态 | 本次真实执行路径 |
|---|---|---|
| fetch_city_boundary_osm | Passed | 浏览器聊天 → Graph → ToolNode → 工具；OSM 响应为合成 Polygon，未在线请求 |
| visualize_layer_on_map | Passed | Point/GeoJSON/HeatMap/Choropleth 创建真实 Layer |
| calculate_buffer_analysis | Passed | 50m 参数传入原有 GIS Buffer，发布派生 Dataset/Layer |
| calculate_area_and_density | Passed | 使用 regions 与 points_csv，执行原有面积、点计数和密度 |
| run_machine_learning_spatial_prediction | Passed | 原有 RandomForestRegressor，100 棵树、random_state=42；真实 fit/predict，生成预测 Layer |
| update_map_style | Passed | 读取前一步创建的 Buffer，修改其样式，浏览器实际反映变化 |
| calculate_feature_distances | Passed | 对真实上传点计算距离并返回 Markdown 反馈 |

单步骤调用（会话 B）、同批按顺序执行的多步骤调用、会话 A 的连续多轮对话全部 Passed。缺失图层工具失败可反馈到浏览器，且 Dataset/Layer 未留下半成品。原有 pytest 继续验证同步/异步、七工具完整名称、参数、默认值、Schema、顺序和绑定能力。

模型输出由 AcceptanceModel 明确脚本化：元数据返回合成 JSON，聊天返回指定 Tool Call，然后根据真实 ToolMessage 作最终响应。这证明执行链路，不证明模型理解自然语言或真实 DeepSeek 响应正确。真实 DeepSeek 和在线 Nominatim 均为 **Not Run**。

## Part VI — Session Isolation

**Passed（真实浏览器）**：A/B 使用不同 Chrome BrowserContext，不是同一会话的两个标签页；各自拥有不同 Streamlit 连接及 SessionData ID。两者同时保持打开，各自通过上传控件导入数据、通过聊天调用工具；Dataset ID 集合无交集，A 的工具执行不改变 B。

**Passed（真实浏览器）**：A 删除 Layer、执行 Reset 后，B 的完整状态快照逐项保持相同；A 的 Dataset/Layer/raw_view/results/messages 清空。Reset 后旧文件不会立刻重新导入。

**Passed（pytest）**：跨会话可变引用隔离、同一 Session 并发工具、异步入口、外来 ID 拒绝及原子发布等既有测试继续通过。KD-SESSION 未退化。

**Not Run**：真实外部模型并发、生产负载和多用户压力测试。本次浏览器会话操作是并存和交错执行，不能描述成生产并发容量验证。

## Part VII — HTML Export

**Passed（真实 Chrome 下载）**：实际点击导出按钮和下载按钮，生成 `downloads/geogpt_map.html`，大小 **72,566 字节**；浏览器收到 HTTP 200，`Content-Type: text/html; charset=utf-8`，`Content-Disposition: attachment; filename="geogpt_map.html"`。CDP `Browser.downloadWillBegin` 的建议文件名也是 `geogpt_map.html`。

下载文件包含实际点、GeoJSON、HeatMap 和红色样式。应用没有写入共享 `map_export.html`。用户按钮名称、下载文件名和 MIME 保持不变。没有以仅捕获 download_button 参数来代替实际下载。

正式页面地图和独立标题 HTML 已在 Chrome 执行。下载所得的完整多图层 HTML 没有另开一次页面逐层视觉检查，记为 **Not Run**；下载内容结构及实际传输已验证。外部 Leaflet/CDN/底图资源可用性仍是离线分享的限制。

## Part VIII — Known Defects and New Findings

下面 13 行均为 **Known Defect**，来自未修改的 strict xfail。共同环境为 Part I 的本机 pytest；共同证据是 `tests/logs/phase4a-regression.txt` 及对应测试源码。每一行的“复现”均可按 Part IX 的命令选择该测试执行。优先级是后续处理建议，本阶段不修复。

| 编号 | 复现步骤/测试定位 | 预期 | 实际 | 影响范围 | 建议优先级 |
|---|---|---|---|---|---|
| KD-CRS | test_gis_correctness.py::test_declared_projected_crs_must_be_transformed；输入带 EPSG:25832 声明的坐标 | 转成约 9E/51N | CRS 被覆盖为 EPSG:4326，数值未正确转换 | 投影输入的空间位置及分析 | P1 |
| KD-BUFFER | test_gis_correctness.py::test_buffer_ground_radius[51]；51N 点 Buffer 500m | Geod 独立参考半径 500m±1% | EPSG:3857 的平面距离不等于该纬度地面距离 | 中高纬 Buffer 解释 | P1 |
| KD-TINY | test_gis_correctness.py::test_tiny_area_retains_nonzero_area_and_density；1m² 区域加内部点 | 面积非零、密度约 1000000/km² | 面积提前舍入到零 | 极小区域面积/密度 | P1 |
| KD-DENSITY | test_gis_correctness.py::test_density_uses_unrounded_area；123.4m² 区域加内部点 | 使用未舍入面积计算密度 | 提前舍入引入误差 | 小区域数值精度 | P1 |
| KD-GEOMETRY/empty | test_gis_correctness.py::test_invalid_area_input_requires_error[empty] | 拒绝空面且不发布成功结果 | 被接受为成功面积结果 | 输入验证/结果可信度 | P1 |
| KD-GEOMETRY/zero_area | 同上 [zero_area]，共线零面积 Polygon | 拒绝零面积面 | 被接受为成功面积结果 | 输入验证/密度解释 | P1 |
| KD-GEOMETRY/self_intersection | 同上 [self_intersection]，自相交 Polygon | 拒绝无效面 | 被接受为成功面积结果 | 无效几何分析 | P1 |
| KD-LOOKUP | test_known_defects.py::test_buffer_can_read_raw_when_other_layer_visible；显示别的图层后以原始名称调用 Buffer | 可读取原始数据 | 旧名称查找仅在显示池中查找 | 名称调用链；ID 路径另有通过测试 | P2 |
| KD-MISSING-POINTS | test_known_defects.py::test_missing_density_input_is_error；指定不存在点层 | 明确错误、不发布密度结果 | 静默降为仅面积的成功结果 | 分析含义/错误反馈 | P1 |
| KD-WEIGHT | test_known_defects.py::test_zero_line_weight；样式 weight=0 | 保存 0 | 真值判断忽略 0 | 样式编辑 | P2 |
| KD-FEATURE | test_known_defects.py::test_single_feature_point_rendering；单个 GeoJSON Feature | 绘制该点 | 点渲染器未迭代单 Feature | 特定点输入格式 | P2 |
| KD-COORDINATES | test_known_defects.py::test_invalid_coordinate_rejected；CSV lat=100 | 拒绝超范围纬度 | 解析接受无效 WGS84 坐标 | 数据质量 | P1 |
| KD-CSV-METADATA | test_session_contracts.py::test_numeric_only_csv_can_complete_frontend_upload；上传 numeric.csv | 正常采样、元数据分析和入库 | numpy int64 不能 JSON 序列化，页面报告解析错误 | 纯数值 CSV 上传 | P1 |

KD-CSV-METADATA 还在真实 Chrome 中复现：Reset A → 上传 `numeric.csv` → 页面显示 `Object of type int64 is not JSON serializable` → Session 无 Dataset/Layer。证据为截图 06、最终 results.json 和 snapshots。它发生在调用元数据模型之前，不能归因于 Mock LLM。

新增记录的既有功能限制（不是新增 pytest xfail，也未认定为本次重构回归）：

| 编号 | 环境/复现步骤 | 预期/验收边界 | 实际 | 归类及影响/优先级 | 证据 |
|---|---|---|---|---|---|
| OBS-KEY-UPLOAD | 真实 Chrome、无 Key 模式；上传合法 points_csv.csv | 无 Key 应给出可理解反馈；是否允许纯解析入库需产品决策 | 显示缺 Key 错误，无法完成上传；README 已说明元数据需要 Key | 既有设计限制；离线用户上传受限，P2 | UI-NO-KEY-UPLOAD，frontend.py:159 |
| OBS-TITLE-UI | 检查正式页面及七工具；查找标题编辑入口 | 本阶段验证已有标题能力，不能添加功能 | Renderer 标题可显示；页面只有标题状态变量和应用逻辑，没有编辑控件 | 用户可达性限制；页面编辑标题 Not Run，P3 | 截图 07，frontend.py:39、199 |
| OBS-METADATA-UI | 上传合法文件后查看 Sidebar 和 Dataset 快照 | 核对当前元数据展示范围 | 名称/类型可见；data_fields 保存但没有字段详情面板 | 既有展示范围；不声明字段详情 UI 已通过，P3 | 截图 03，frontend.py:162、168 |

浏览器最终记录 **0 未捕获 JavaScript 异常、0 console error、0 Network.loadingFailed**；有 **37 条 warning**，主要为 Streamlit iframe Permissions Policy 特性名和 sandbox 提示，已保留日志，不将其描述成“控制台完全无消息”。

验收基础设施调试事实：初版曾出现脚本语法问题、受限执行环境下 Chrome GPU/renderer 启动失败、页面导航早期 DOM 为空、点图标 CSS 选择器不匹配，以及 Streamlit rerun 期间的按钮/上传控件时序问题。仅修正独立验收脚本：等待真实可操作元素、发送鼠标输入、等待 Reset 后的新控件并使用 marker pane 定位；没有修改业务断言或生产实现。先前失败日志保留在 `tests/logs/phase4a-*/failure.txt`，最终成功运行目录见下文。最终 Chrome 验收需要允许浏览器渲染子进程正常运行；受限沙箱内的早期尝试不算 Passed。

## Part IX — Actual Test Commands and Results

实际执行：

```powershell
.venv\Scripts\python.exe tests/check_startup.py
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe tests/phase4a_acceptance.py
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short | Tee-Object -FilePath tests/logs/phase4a-regression.txt
git diff --check
git status --short
git rev-parse HEAD
git rev-parse main
```

| 命令/检查 | 状态 | 实际结果 |
|---|---|---|
| 原启动脚本 | Passed | HTTP/health/WebSocket 页面执行成功，服务自动停止 |
| 首次完整 pytest | Passed + Known Defect | 208 passed、13 xfailed，25.32s，退出码 0 |
| 最终 Chrome 验收 | Passed + Known Defect + Not Run | 23 Passed、1 Known Defect、2 Not Run，退出码 0；最终记录无 Failed/Blocked |
| 新脚本完成后的完整 pytest | Passed + Known Defect | 208 passed、13 xfailed，26.46s，退出码 0；0 意外 Failed、0 XPASS、无跳过 |
| 已有文件 diff / whitespace | Passed | `git diff --name-only` 和 `git diff --check` 无输出；两个新脚本另经 AST 和行末空白检查 |
| backend.py / refs | Passed | backend 不存在；HEAD/main 与验收前一致 |

最终证据目录（本地保留，受现有 `.gitignore` 忽略）：

`tests/logs/phase4a-46fc9d498dda485294fb87592640e0d2/`

- `results.json`：26 条最终验收状态记录。
- `01-no-key-initial.png` 至 `07-standalone-title.png`：真实 Chrome 截图。
- `snapshots/*.json`：测试包装器在真实页面运行后读取的业务状态，数据均为合成输入。
- `initial-map-dom.json`、`browser-events.json`、`cdp-events.json`：实际 DOM、JavaScript、网络及下载证据。
- `downloads/geogpt_map.html`、`title-map.html`：真实下载及正式 Renderer 生成的标题样本。
- `fixtures/`：可重复生成的小型 CSV/JSON/GeoJSON。
- `no-key-server.log`、`mock-server.log`、`chrome.log`：本机服务和浏览器日志。
- `cleanup-check.json`：两个 Streamlit 端口 56728/52373 和 Chrome CDP 端口 65228 均已关闭；此前脚本已 wait 自有进程。

原正式入口启动证据：`tests/logs/phase3e-startup-server-4a6ef71cc82648c3a808976fd5f542a1.log`。全量回归输出：`tests/logs/phase4a-regression.txt`。没有将日志、浏览器缓存、临时 profile 或下载产物纳入版本控制。

全系统 WMI 进程枚举在受限环境中被拒绝，未据此声称全机没有其他 Streamlit。清理结论基于本次持有的子进程句柄、wait 和自有端口探测，不影响该结论。

单个历史缺陷可复现，例如：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_session_contracts.py::test_numeric_only_csv_can_complete_frontend_upload -q -ra --tb=short
```

该单例命令为后续复现说明，本阶段实际执行的是包含该用例的完整测试集。

## Part X — Acceptance Assessment

**主要链路验收通过，带已知缺陷和明确未执行范围。** 真实浏览器完成上传 → Mock 决策 → 真实 Graph/七工具/GIS/原有随机森林 → Session → Folium 展示 → 实际 HTML 下载。双独立浏览器会话的数据、工具和 Reset 隔离已验证。

13 个原 strict xfail 全部保留，独立 GIS 参考断言未改。没有新增业务回归；没有把 Mock 模型、HTTP/WebSocket 或 HTML 结构检查描述成真实外部服务/浏览器视觉验证。

仍未验收：真实 DeepSeek、在线 Nominatim、自由自然语言理解、跨浏览器人工体验、生产负载，以及下载的完整多图层 HTML 的独立逐层视觉复核。标题 Renderer 浏览器能力通过；页面标题编辑入口本来不存在，不能声明其用户操作通过。

交付文件仅三个新增文件：

1. `tests/acceptance_app.py`：独立测试包装入口。
2. `tests/phase4a_acceptance.py`：Chrome/CDP 验收、合成数据、Mock 边界和证据生成。
3. `tests/PHASE4A_REPORT.md`：本报告。

业务源码、原有测试、依赖、README、配置均无修改。工作区预期只有这三个授权新增文件，未暂存、未提交。HEAD/main 保持原值，backend.py 不存在。仅完成 Phase 4A，停止等待用户审核。

## Phase 4A Finalization — 审核与证据归档

### 1. 审核结论

审核起点重新确认为 `refactor/modular-architecture` / `5330368ddb633c3bff8edc5d916d60dc1a692cf8`。只有本报告和两个验收脚本未跟踪，未发现来源不明的文件或已跟踪修改。README、正式业务源码和既有测试与 HEAD 保持一致。

**Code-confirmed**：验收包装器确实执行正式 `frontend.py`，没有替换 Graph、ToolNode、GIS、Renderer、Session 或随机森林。**Test-verified**：加强后的验收脚本重新通过真实 Chrome 全链路；完整 pytest 仍为 208 Passed、13 strict xfail。没有通过修改业务断言配平数量。

### 2. 浏览器真实性与 Mock 边界

Chrome 156.0.8078.47 的 CDP、真实 DOM 文件输入、键盘聊天输入、鼠标按钮事件、Leaflet DOM、实际网络下载事件均有记录。文件上传使用 `DOM.setFileInputFiles` 操作正式控件，聊天经 `frontend.py:227` 的真实 Agent 入口进入 `agent/graph.py` 的 ToolNode，七工具继续调用正式 GIS/Maps/Core。

Mock 只提供预先声明的 LLM 元数据/Tool Call 响应和合成 OSM 响应；另外隔离真实凭据来源和禁止服务端外部网络。业务数据由真实解析器读取，实际工具返回的 ToolMessage 被模型包装为页面反馈。描述性的 Passed 文本位于真实操作和断言之后，不是预生成结果。独立标题样本直接调用正式 Renderer，明确不计作页面标题编辑。

两个 mock BrowserContext 在两个独立 Streamlit 页面中建立不同 SessionData，真实上传、执行工具、删除、Reset。最终 CDP 记录共有 3 个 Streamlit WebSocket 请求（无 Key、A、B）；状态快照有 3 个独立业务会话。独立标题文档不属于 Streamlit 会话。没有把两个标签页冒充两套业务会话。

### 3. CSV 缺陷和无 Key 限制

**Known Defect / Test-verified**：KD-CSV-METADATA 再次通过真实浏览器复现。`gis/data_io.py` 的 CSV 解析可完成，但纯数值行的属性含 numpy int64；`frontend.py:151` 在 `json.dumps(raw_data[:3])` 采样时失败，尚未访问 LLM，也未调用 `add_upload`。13 个 strict xfail 的源码没有变化。

**Code-confirmed / Test-verified**：无 Key 时使用含 name 字段的正常 CSV，解析和 JSON 采样通过；`frontend.py:159` 为 AI 元数据分析求值 `session_agent.llm`，在 `AgentRuntime.model → create_llm` 的延迟初始化时抛出缺 Key 错误。因此尚未进入元数据函数的调用体，也未到 `frontend.py:166` 的 Dataset 注册。这不是纯解析或 Dataset 容器需要 Key。

前端捕获错误并显示 `Fehler beim Parsen ... DEEPSEEK_API_KEY wurde nicht gefunden!`。反馈包含具体原因，但统一冠以“解析错误”，阶段名称不够精确；当前仍无法完成无 Key 上传。此为 README 已说明的既有功能限制，本阶段未修复。元数据函数内部的 fallback 无法捕获发生在参数求值阶段的错误。

### 4. Browser Warning 分类

首次验收和本轮最终复测的已采集 Log 均为 37 条 warning、0 console error。按原始 `source`、文本和 URL 分类如下；计数可能随组件重建次数变化，脚本没有将 37 写成必须匹配的断言。

| 类别 | 数量 | 来源和具体类型 | 风险判断及验证边界 |
|---|---:|---|---|
| 浏览器/第三方兼容性提示 | 24 | Streamlit 页面 `react-dom.CbzydqoA.js` 设置 iframe 策略时，Chrome 报 `Unrecognized feature`；8 个名称各 3 条 | 本轮 GeoChat 功能未观察到故障；未识别策略名不能当作策略已有效应用。当前页面未使用这些传感器/VR 功能，建议 P3 兼容性跟踪 |
| iframe 安全隔离提示 | 10 | `streamlit_folium.st_folium/index.html`；同时使用 allow-scripts / allow-same-origin 的 sandbox 警告，source=security | 提示组件脚本可能削弱 iframe 隔离，不能认定无风险。本阶段未验证利用链，也未进行不可信 HTML/脚本攻击测试；建议 P2 独立评估组件边界，而非在本阶段修改业务或依赖 |
| Canvas 渲染性能提示 | 3 | Folium `leaflet_heat.min.js` 的 getImageData 多次读取，source=rendering，提示 willReadFrequently | 小型 HeatMap 实际显示通过；大数据绘图性能 Not Run，不能据此排除性能影响，建议 P3 |
| 明确的 API 弃用通知 | 0 | 已采集的 37 条 Log 中无明确 deprecated/deprecation 条目 | 不等于证明所有依赖都没有弃用 API；未识别 feature 名称归入上一类兼容性提示 |

8 个未识别名称分别是 ambient-light-sensor、battery、document-domain、layout-animations、legacy-image-formats、oversized-images、vr、wake-lock。

Canvas 提示的原始来源 URL 是 `https://cdn.jsdelivr.net/gh/python-visualization/folium@main/folium/templates/leaflet_heat.min.js`。因此必须保留环境边界：服务端测试不依赖真实外部业务 API，但真实浏览器仍使用 CDN JS/CSS/图标及 OSM 底图，**不是完全离线测试**；其中 @main 资源也不是固定内容版本。当前环境两轮均成功，不保证断网或 CDN 内容改变后的重复结果。冻结这些资源需要独立的依赖/资源管理决策，本阶段没有改变正式 Renderer 的资源策略，也没有以假地图掩盖资源不可达。

### 5. 覆盖范围和 Not Run

- **Test-verified，真实浏览器**：上传、错误输入反馈、点/线/面/MultiPolygon、热力图、分级设色、SVG 样式、图层开关、删除、Reset、独立会话、实际 HTML 下载。
- **Test-verified，Mock 决策 + 真实业务执行**：七工具注册/绑定、单步、多步、多轮、工具失败、GIS 派生图层、原有随机森林；不证明自然语言理解质量。
- **Not Run，功能存在但未测试**：真实 DeepSeek、在线 Nominatim、下载后完整多图层 HTML 独立逐层视觉检查、跨浏览器人工体验及生产负载。
- **Not Run，页面能力不存在**：标题编辑控件；Renderer 标题 API 存在且通过独立浏览器验证，但正式页面没有编辑入口，不能列作“已有页面操作待测”。
- 原有 13 项 **Known Defect** 全部保留；浏览器的 1 项 Known Defect 仍指 KD-CSV-METADATA，不是第 14 项。

### 6. 脚本质量、最小修正与资源清理

本轮只修改 `tests/phase4a_acceptance.py` 的验收基础设施，保留所有原有业务断言并加强：

1. 注册检查由数量提升为七个完整工具名称及顺序，真实地图 SVG path 下限由 3 提升到 4。
2. 下载要求观察到 Chrome completed 事件、正确文件名、HTTP 200、text/html、Content-Disposition，且无 .crdownload 临时文件；仍检查下载内容。
3. console error 或失败网络请求也使浏览器诊断项失败，warning 单独统计到 `browser-diagnostics.json`。
4. 检查项失败额外保留 traceback 和当前浏览器截图，不只记录一句 Failed。
5. 清理逐个处理自有 Popen 句柄和日志；某项异常仍继续清理其他自有资源，最终记录失败。给 Browser.close 最多 5 秒正常退出，再由已有句柄兜底。记录自有 PID、退出码、Chrome/Streamlit 端口和错误列表，不能清理其他会话的任意进程。

Chrome 使用独立临时 profile、动态调试端口和临时 BrowserContexts；Streamlit 使用动态端口。路径由仓库位置及 PROGRAMFILES 推导，无个人绝对路径。测试数据每次由脚本确定性生成，不包含私人数据。不安装浏览器包或新运行依赖，不读取真实 Key/.env/secrets。

最终 Chrome PID 22932 正常退出码 0；两个自有 Streamlit 子进程经 terminate/wait 回收，退出码 1 是主动终止的记录，不是验收失败。三个端口 65229、64521、54213 均关闭，cleanup.errors 为空。未使用全局进程名批量终止或全机端口清理。

### 7. 本轮实际测试

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe tests/phase4a_acceptance.py
git diff --check
```

**Test-verified**：完整 pytest 为 **208 passed、13 xfailed，23.52s，退出码 0**；原有 221 项测试不变，0 意外 Failed、0 XPASS。修改清理后再次运行完整浏览器脚本，仍为 **23 Passed、1 Known Defect、2 Not Run，退出码 0**。浏览器多次复核的计数一致，没有为匹配旧数量增删测试。

浏览器复测通过允许正常渲染子进程的执行环境运行；不是将受限沙箱下的 renderer 阻塞计为通过。最终 trace 同时验证 0 未捕获 JS 异常、0 console error、0 网络失败，37 条 warning 分类如上。

### 8. 证据归档与提交范围

最新、与最终脚本对应的证据目录：

`tests/logs/phase4a-44f3c84dab3c4f00a33fdaab03467fc1/`

之前的 `phase4a-46fc9d498dda485294fb87592640e0d2/` 保留为首次验收证据；本轮中间复测 `phase4a-61e4ccf84cc34cf4ae128f74e53a0c2f/` 也未删除。仅最新目录作为本次 Finalization 的最终浏览器证据。

以下 SHA-256 用于本地证据核对，原始大日志/截图/下载/浏览器 profile 不提交：

| 最新证据相对路径 | SHA-256 |
|---|---|
| results.json | `6011edf554cd36480f5e2aef2a045500dd553d8a3c729f973e87fba8605deb32` |
| browser-diagnostics.json | `85c427887f8a400aa05868d5c19fe168c092b75a40c3382934e4674ac75700b3` |
| cleanup-check.json | `f7f41fcb2fe68b18bc66b55944ddfa51b04cd053cccbc9c43bcbec804cf009e7` |
| cdp-events.json | `83f7238888fef4e13d71bac7806a1d24d3efcb3e2d5a534682dc4a3e0f651e25` |
| downloads/geogpt_map.html | `2a217bb9b7fb1adda92ca9d61a35c53d802576cb0affa39385dac8b05fb269e6` |
| 04-heatmap-choropleth.png | `ca5d24e88b7bb423a587b05528fab2f7cc269e7c87e230cd138d14cff12df2a7` |
| 06-numeric-csv-known-defect.png | `114317b13b95473f37c500910d25c5b2ad56929838db02f0599fd7de4ec82a31` |

只纳入三个 Phase 4A 文件：`tests/acceptance_app.py`（真实页面测试包装器）、`tests/phase4a_acceptance.py`（验收脚本及确定性数据生成）、`tests/PHASE4A_REPORT.md`（实施与最终审核报告）。原始证据只保留本地，相对路径和摘要随本报告入 Git；不把本地保存误称成原始截图已经随 Git 分发。

### 9. Git 基线固定

仅对上述三个文件创建独立本地提交 `test: add browser acceptance coverage`。提交对象哈希以包含本报告的 Git Commit 和最终交付消息为准，不在报告中自引用。main 继续保持 `dcad948d029842a555c609149b5cc226a1fdfe90`，backend.py 仍不存在。既有源码、测试、依赖及 README 没有 diff；没有 Push、Merge、Rebase 或 Reset。

### 10. 最终验收判断

在本次已验证的浏览器及资源可达环境中，Phase 4A 审核通过，可固定测试交付基线。Mock 边界、Known Defect、37 条 warning 的来源和剩余风险、Not Run 及功能不存在项均已区分。没有将静态检查视为真实外部服务测试，也没有宣称 browser 完全离线或所有 warning 无风险。

仅完成本阶段审核与本地提交，不修复 GIS/CSV/Agent，不开展后续开发。
