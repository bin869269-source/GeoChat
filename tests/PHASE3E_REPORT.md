# GeoChat — Phase 3E: 最终架构整合与兼容层清理

> 前十部分保留实施阶段记录；最终验收审核及最新结果见文末 Finalization。
> Finalization 最终全集为 221 项：208 Passed、13 strict xfail，无意外失败或 XPASS。

## Part I — Integration Summary

开始时实际核实：分支 refactor/modular-architecture，HEAD
be5fac920e01674d5227aff4b225d2b393d29e84，工作区和暂存区干净；
main=dcad948d029842a555c609149b5cc226a1fdfe90。没有来源不明的修改或项目约束文件。
先实际运行原基线：208 项，195 Passed、13 strict xfail，17.22s。

完成前端正式导入、SessionAgent 正式归属、测试调用迁移与 backend.py 删除。
新增应用集成测试、可重复的真实服务启动检查、根 README，并更新测试说明。
没有改变 GIS/地图/Core 实现、七工具业务、模型配置、依赖或忽略规则。

最终实际全集：**216 项，203 Passed、13 strict xfail、0 意外 Failed、0 XPASS，23.37s**。
八个新增集成用例全部通过。没有 Commit、Push、Merge、Rebase、Reset 或开始后续阶段。

证据分类：Code-confirmed=源码/AST/依赖检查；Test-verified=实际执行；
Known Defect=strict xfail，不能解释为通过；Not Run=没有实际验证的外部或浏览器行为。

## Part II — Final Architecture and Dependency Review

核心代码已经达到批准的目录，仓库名称仍为 GeoChat-Sync：

```text
frontend.py
agent/{__init__,graph,tools,llm}.py
gis/{__init__,data_io,geometry,statistics}.py
maps/{__init__,renderer}.py
core/{__init__,contracts,session}.py
tests/
```

Code-confirmed：

- frontend.py:10 起直接依赖 agent.graph、agent.llm、core.session、gis.data_io、maps.renderer。
- agent.graph:147 SessionAgent 及 :176 create_agent_session 管理会话绑定与延迟执行，
  工具在构造实例时从 agent.tools 取得；不从 backend 或 maps 取得功能。
- Graph 原 agent_reasoner、should_continue、build_workflow、create_agent_app 的 AST 完全不变。
- agent.tools 继续依赖 GIS/Core 和原请求/数值库；agent.llm 仍只在实际模型创建时读取配置。
- GIS 无 Agent/Streamlit/模型依赖，Maps 无 Agent/Session 依赖，Core 仅有标准库和本包契约。
- SessionAgent 采用明确属性替代通用 __getattr__；没有动态静态函数代理或全局默认 Agent。
- 没有增加业务状态全局对象；七工具仍由显式 Session 的工厂闭包绑定。

Test-verified：正式模块不同顺序的独立进程导入、AST 依赖方向/循环检查，以及既有
GIS、Maps、Core 的禁止框架导入测试均通过。独立进程不继承真实 Key，阻断外部连接及模型提供方初始化。
实际构建 Mock 模型/真实 Graph 验证正式工厂可用，没有只靠静态搜索判断安全。

## Part III — backend.py Compatibility Removal

修改前已列出门面入口，并在修改前给出迁移清单。项目内生产调用方只有 Frontend；
其余调用位于测试 fixture、测试函数、反射检查和动态 spec_from_file_location 加载。
历史报告中提到 backend 的文本是历史记录，不是可执行依赖；保留 Phase 1–3D 成果。

| 原门面入口 | 正式归属 / 迁移方式 | 已检查的项目内调用 |
|---|---|---|
| SessionAgent、create_agent_session | agent.graph；前端直接导入 | Frontend、会话/Agent/集成测试 |
| AgentState、AgentRuntime、should_continue | agent.graph | Graph/路由/运行时测试 |
| handle.agent_app、llm、llm_with_tools、workflow、get_agent_layers | 正式 SessionAgent 的同名执行/模型属性 | Frontend、同步/异步、多步骤及初始化测试 |
| create_llm | agent.llm | 模型配置与无 Key 测试 |
| smart_analyze_geodata、handle.smart_analyze_geodata | agent.llm.smart_analyze_geodata，显式传 llm=agent.llm | Frontend 上传、元数据/模型复用测试 |
| handle.agent_reasoner | agent.graph.agent_reasoner，显式模型与 get_layers | 模型复用/Prompt 测试 |
| set_agent_context、handle.set_agent_context | core.session.SessionData.from_legacy / session.replace_from_legacy | fixture、会话、Graph 和状态测试 |
| SessionData | core.session | Frontend 与会话测试 |
| create_tools、七工具实例 | agent.tools.create_tools(session) 或显式 Agent 的 TOOLS/同名属性 | 注册、Schema、七工具正常/错误分派测试 |
| make_unique_layer_name、clean_gdf_for_geojson | agent.tools；属性清理仍委托 gis.data_io | 工具、清理行为测试 |
| parse_uploaded_file、extract_features_to_gdf | gis.data_io | Frontend、导入/GIS/正确性测试 |
| buffer_geometries、pairwise_distances | gis.geometry | GIS、工具委托与回归测试 |
| gis_statistics 模块别名 | gis.statistics | Area/Count/Density 及委托测试 |
| DEFAULT_MAP_STYLE、create_layered_map | maps.renderer | Frontend、地图/样式测试 |
| add_points_layer、add_heatmap_layer、add_choropleth_layer、add_geojson_layer | maps.renderer | 公开入口、各地图类型与缺陷测试 |
| Titel_hinzufügen、render_map_html | maps.renderer | Frontend 标题/下载及 HTML 测试 |
| requests、agent_llm 模块别名 | 直接使用 requests 或 agent.llm；不再重导出 | Nominatim Mock、模型测试 |

删除顺序与运行证据：

1. 迁移 Frontend 的直接导入、AI 元数据调用，以及所有测试 fixture/辅助调用。
2. 测试基础设施加入运行时 backend 导入禁止器，捕获后吞掉的导入也会失败。
3. backend.py 仍存在时完整运行迁移后的 208 项：195 Passed、13 strict xfail，16.30s。
4. 没有隐藏导入被触发，再删除 backend.py；删除后又运行新增集成测试和完整全集。
5. 新集成测试明确验证文件不存在，正式入口可调用；真实 Streamlit 服务也在删除后完成页面执行。

没有未迁移的项目内接口调用。旧门面本身及其动态代理有意移除，不能宣称旧外部 API 全部兼容。
未知外部脚本若仍 import backend 或调用 handle.set_agent_context / smart_analyze_geodata / agent_reasoner，
必须按表迁移。外部脚本未提供、Not Run；不恢复全局默认 Session 来掩盖该变化。

仍保留的旧格式适配位于合理职责边界：core 的 LegacyLayerView/from_legacy/replace_from_legacy，
以及工具的显式属性清理辅助。它们用于保留数据/地图行为，没有另建过渡模块、状态仓库或动态门面。

## Part IV — Frontend and Agent Integration

Code-confirmed：FrontEnd 仅改变正式导入、元数据调用的显式模型传参，移除两个未使用导入；
UI 文本、地图调用参数、HTML 下载文件名/MIME、Session 初始化、删除和 Reset 策略保持。
SessionAgent 从门面迁入 agent.graph，保留模型/Graph 单次初始化、七工具属性与 invoke/ainvoke。
不再携带 GIS/Map 静态代理、元数据和 reasoner 的冗余包装、set_agent_context 旧方法。

Test-verified：

- 三种上传格式分别执行完整 AppTest 链：真实解析 → Mock 元数据 → 显示 → Buffer → 样式更新 →
  HTML 字节下载 → 工具失败反馈 → 删除显示层 → Reset。
- 元数据与 Graph 复用同一模型，绑定一次；ScriptedModel 返回独立的绑定对象，防止错误接线被 Mock 掩盖。
- 导出仍为 geogpt_map.html / text/html，HTML 包含点、面及已提交样式，不写共享固定文件。
- 两个 AppTest Agent 使用同名但不同数值的数据，先后执行均取得自己的值与 Dataset ID。
- 既有 Barrier 线程并发、同会话并发、公开异步 gather、ToolNode 顺序、多步骤、原子失败和局部重置继续通过。
- 真实 scikit-learn 随机森林预测/残差继续通过，Nominatim 使用 Mock，七工具业务和 Schema 未改。

AppTest 上传控件和模型回答使用边界替身，不等于真实浏览器上传或真实 LLM 理解。

## Part V — GIS, Maps and Core Regression

gis/、maps/、core/、agent/tools.py、agent/llm.py 对 HEAD **无 diff**。
没有改 CRS、Buffer、Distance、Area、Count、Density、边界点、精度或无效几何策略。
地图 bounds、中心、样式、色带、LayerControl 和顺序由原实现及测试继续保护。

测试迁移仅更换接收者/导入：backend fixture 改为正式 session_agent，原函数代理改为模块直接调用，
旧 set_agent_context 改为该 Session 的 replace_from_legacy。纯函数用例不再请求无用的 Agent fixture；
缺 Key 测试保留 fixture 隔离 Streamlit secrets 和客户端构造。

两项旧门面重导出的对象身份测试改为正式入口检查：地图函数的归属、可调用性及原默认样式字典断言；
Data I/O/Geometry/Statistics 的实际调用与结果字段检查。这两项不再要求已删除门面继续重导出函数。
它们不是 GIS 数值正确性参考，原数值/容差/输入数据断言全部保留。

实际 AST 检查确认：test_gis_correctness.py、test_known_defects.py 的所有 Assert，
在仅规范化导入接收者后与 HEAD 一致；KD-CSV-METADATA 整个测试除 fixture 名称外 AST 一致。
没有删除用例或以跳过代替回归；原 208 个用例全部继续执行。

## Part VI — Application Startup Verification

Test-verified：新增 tests/check_startup.py，以已有 Streamlit/websockets 依赖启动真实独立服务，
不安装额外依赖。WebSocket 客户端来自已安装的 websockets 16.1.1，Streamlit 1.66.1 本身声明该依赖。

实际入口：

```powershell
.venv\Scripts\python.exe tests/check_startup.py
```

脚本启动与停止自己创建的子进程，等价 CLI：

```powershell
.venv\Scripts\python.exe -m streamlit run frontend.py --server.address 127.0.0.1 --server.port <临时端口> --server.headless true --browser.gatherUsageStats false --server.fileWatcherType none
```

实际通过：

- 独立子进程不继承 DeepSeek Key、代理或追踪凭据，禁用遥测；只监听本机回环。
- /_stcore/health 返回 HTTP 200 / ok；根页面 HTTP 200 且提供 Streamlit HTML。
- 建立真实 /_stcore/stream WebSocket 会话，发送页面执行请求。
- 收到 GeoGPT 标题、上传控件、聊天输入和地图组件；收到 FINISHED_SUCCESSFULLY，无异常元素。
- 结束后关闭 WebSocket 并终止自有服务进程，没有留下后台服务。

这比仅请求静态首页更进一步，实际执行了删除 backend 后的 Frontend。
Not Run：浏览器 JavaScript、瓦片/外部 CDN 加载、视觉/真实点击下载、真实模型或线上 Nominatim。

首次手工检查脚本过度精简子进程环境，漏掉 Windows USERPROFILE，导致框架启动前找不到 home。
修正为继承原 Windows profile 变量（不改 HOME/USERPROFILE、不改业务源码）后，正式检查通过。
首次失败与最终日志均保留，不将测试环境问题记成业务修复。

## Part VII — Known Defects and Unverified Behaviors

最终 13 个 strict xfail 与 Phase 3D 的组成相同，均 strict=True、raises=AssertionError：

- KD-CRS、KD-BUFFER：CRS 覆盖、51N 地面 Buffer 误差。
- KD-TINY、KD-DENSITY：提前面积舍入及密度精度。
- KD-GEOMETRY 三项：空面、零面积、自交输入。
- KD-LOOKUP、KD-MISSING-POINTS：名称查询遮蔽、缺点图层静默降级。
- KD-WEIGHT、KD-FEATURE、KD-COORDINATES：零线宽、单 Feature 点渲染、坐标范围。
- KD-CSV-METADATA：纯数值 CSV 的 numpy 标量采样序列化。

KD-SESSION 仍是正常通过的隔离用例，没有重新标为 xfail。没有 XPASS 或新增 Known Defect。
不将结构整合描述为 GIS 正确性已解决。历史对话轨迹缺失、内存会话、不自动清理孤立 Dataset、
复制成本及旧格式载荷等此前已记录的限制仍在。

Not Run 还包括：未知外部脚本、生产并发/大数据、进程故障恢复、热升级及其他 Python/操作系统版本。

## Part VIII — Actual Test Commands and Results

环境实际为 CPython 3.14.5、pytest 9.1.1、Streamlit 1.66.1；现有版本及配置未升级。

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe -m pytest tests/test_app_integration.py -q -ra --tb=short
.venv\Scripts\python.exe tests/check_startup.py
git diff --check
```

| 实际运行 | 总数 | Passed | strict xfail | 意外 Failed / XPASS |
|---|---:|---:|---:|---:|
| 修改前完整基线，17.22s | 208 | 195 | 13 | 0 / 0 |
| 首次迁移，测试误用字段 | 208 | 194 | 13 | 1 / 0 |
| 修正测试字段，backend 存在但禁止导入，16.30s | 208 | 195 | 13 | 0 / 0 |
| 删除 backend 后新增集成，14.15s | 8 | 8 | 0 | 0 / 0 |
| 删除后完整回归，25.24s | 216 | 203 | 13 | 0 / 0 |
| 最终完整回归，23.37s | 216 | 203 | 13 | 0 / 0 |

首次新入口功能测试误写 area_m2；真实公共接口只返回 area_km2。
仅纠正该新增入口测试的字段名，没有修改 GIS 实现、独立参考或原正确断言。
最终 pytest 退出码 0；git diff --check 通过，未跟踪源码另做语法/行尾/路径检查。

日志保留本地且被现有 .gitignore 排除，不提交：

- tests/logs/phase3e-baseline.log
- tests/logs/phase3e-migrated-before-removal.log（首次字段错误）
- tests/logs/phase3e-migrated-before-removal-fixed.log
- tests/logs/phase3e-integration-run.log
- tests/logs/phase3e-run.log、phase3e-final-run.log
- tests/logs/phase3e-startup-server.log、phase3e-startup-check.log（首次环境失败）
- tests/logs/phase3e-startup-server-final.log、phase3e-startup-final.log

没有用真实 Key、DeepSeek 或 Nominatim 完成自动化测试；所有外部请求边界继续受阻断断言保护。

## Part IX — Git Diff and Changed Files

18 个文件，均归属 Phase 3E；未暂存、未提交：

| 文件 | 实际变化 |
|---|---|
| agent/graph.py | 正式承接 SessionAgent/工厂，保留原运行时和 Graph 算法 |
| backend.py | 在迁移及运行验证后删除 72 行兼容门面 |
| frontend.py | 正式模块直接导入、元数据显式传模型，移除无用导入 |
| README.md | 新增应用环境、启动、架构、正式 API 和外部迁移说明 |
| tests/README.md | 更新当前测试入口和边界，区分历史快照 |
| tests/conftest.py | 正式工厂 fixture、移除动态 backend 加载、禁止旧导入 |
| tests/test_agent_modules.py | 正式模型/Graph/GIS 委托入口，保留绑定/初始化/分派断言 |
| tests/test_agent_session.py | 正式入口、上下文导入和 AppTest fixture 适配 |
| tests/test_data_maps.py | 直接 Data I/O、LLM、Maps 调用 |
| tests/test_gis_core.py | 正式公开入口与工具清理辅助检查 |
| tests/test_gis_correctness.py | 只迁移 fixture/数据转换入口，保留数值参考 |
| tests/test_known_defects.py | 正式入口接收者，原业务断言与标记保留 |
| tests/test_map_renderer.py | 旧重导出身份检查改为正式函数归属/默认样式检查 |
| tests/test_session_contracts.py | 正式工厂导入、fixture 名称和显式旧上下文导入 |
| tests/test_tools.py | 正式会话工具、GIS 和 Nominatim Mock 调用 |
| tests/test_app_integration.py | 八项删除后架构/应用集成验证 |
| tests/check_startup.py | 可重复真实本机 HTTP/WebSocket 启动检查及进程清理 |
| tests/PHASE3E_REPORT.md | 本报告及证据/兼容边界 |

gis/、maps/、core/、agent/tools.py、agent/llm.py、依赖声明和 .gitignore 不变。
旧报告、CASE_RESULTS.md 与历史版本/其他仓库未修改。
Frontend 的原 Windows 只读属性在授权修改结束后恢复；backend 删除为明确批准的本阶段目标。
日志、缓存、环境及凭据不在项目变更清单；候选文件未发现真实密钥或硬编码本机绝对路径。

分支/HEAD 仍为 refactor/modular-architecture / be5fac920e01674d5227aff4b225d2b393d29e84；
main 仍为 dcad948d029842a555c609149b5cc226a1fdfe90。没有执行 Git 写操作。

## Part X — Remaining Risks and Acceptance Assessment

Code-confirmed + Test-verified：批准的核心目录已完整，内部不依赖 backend，正式入口、七工具、
Session 隔离、GIS/地图回归以及实际服务页面执行通过，具备 Phase 3E 审核条件。
当前没有因模块归属冲突而被删除的产品功能，也没有仍待迁移的项目内调用方。

已知缺陷与未验证行为独立保留；本结论不是全功能生产验收、外部脚本兼容保证或 GIS 精度认证。
启动辅助使用已固定 Streamlit 协议和已安装依赖；未来升级框架时应同步审核该检查。
未知外部旧脚本必须按迁移表处理，不能继续 import backend；大数据复制/内存限制和完整浏览器验收仍待独立安排。

只完成 Phase 3E。所有修改留在工作区等待用户审核，不自动提交或开展缺陷修复、部署及下一阶段。

## Finalization — 最终架构验收与本地 Git 提交

### 1. 最终审核结论

本次独立重新核实 refactor/modular-architecture / be5fac920e01674d5227aff4b225d2b393d29e84，
暂存区为空，全部 18 个修改/删除/新增文件与上次交付一致，未发现其他成员或来源不明的工作内容。
原 216 项基线实际复跑：203 Passed、13 strict xfail，25.82s。
审核实际源码、上一提交及全部候选文件，不只核对目录存在或引用上次报告。

结论：架构整合及自动化回归满足本阶段验收；启动验证脚本的端口选择和退出验证作最小加固，
新增五项基础设施测试。业务源码、GIS 历史缺陷、七工具和 Session 语义没有在 Finalization 中修改。

### 2. 核心目录与模块依赖

Code-confirmed：所有批准的 Package 和 __init__.py 完整，backend.py 已不存在。
Frontend 直接从正式模块导入；旧 Graph 函数和运行时除文档外的 AST 与父提交一致。
gis/、maps/、core/、agent/tools.py、agent/llm.py、依赖及配置相对父提交无 diff。
没有全局默认用户或当前 Session，模型仍延迟初始化，Dataset 的权威所有权未改变。

Test-verified：不同顺序独立导入、实际 Graph 执行、架构依赖方向和循环检测继续通过。
动态导入中保留的是测试对子模块导入顺序的验证，不是旧 backend 加载。
工具工厂的局部导入属于正式 agent 模块，未形成循环。

### 3. backend 删除与正式接口

重新从父提交读取 backend 全部 72 行，核对本报告 Part III 迁移表：Agent 创建/运行属于 agent.graph，
模型/元数据属于 agent.llm，工具和辅助属于 agent.tools，数据/计算属于 gis，地图属于 maps，状态属于 core。
没有发现丢失的项目内功能或未迁移的旧调用方；普通/动态 backend 导入禁止器在全集中保持有效。

保留的内部格式适配明确限于 core.session 的 LegacyLayerView、from_legacy/replace_from_legacy，
以及 agent.tools 的显式属性清理辅助；它们服务现有格式和 Renderer 边界，无旧门面依赖或额外数据仓库。
有意取消的门面重导出、动态属性及 set_agent_context 包装按 Part III/README 迁移。
未知外部脚本没有提供，Not Run，不宣称它们保持旧 import backend 接口兼容。

### 4. 九个既有测试文件专项审核

实际 AST 逐函数比对：**128 个原测试函数全部保留**，重命名映射明确；
参数化及 xfail 装饰器 AST 均一致。规范化 fixture 名称、模块接收者、显式模型/上下文传参和局部导入后：

| 文件 | 原测试函数数 | 函数体差异 |
|---|---:|---|
| test_agent_modules.py | 20 | 无 |
| test_agent_session.py | 11 | 一项无 Key 入口测试 |
| test_data_maps.py | 12 | 无 |
| test_gis_core.py | 10 | 一项旧 GIS 重导出检查 |
| test_gis_correctness.py | 13 | 无 |
| test_known_defects.py | 6 | 无 |
| test_map_renderer.py | 12 | 一项旧地图重导出检查 |
| test_session_contracts.py | 29 | 无 |
| test_tools.py | 15 | 无 |

三处有意差异的等价验证：

- 无 Key 测试不再通过 spec_from_file_location 执行已删除文件，改为正式工厂冷创建，
  仍断言模型未初始化，并保持 create_llm 缺凭据时相同的 ValueError；fixture 仍隔离 secrets 和客户端构造。
- GIS 重导出身份比较改为正式 Data I/O、Geometry、Statistics 的真实调用及结果检查。
  原门面的函数别名不再是存在条件；独立 GIS 数值参考未修改。
- 地图重导出身份比较改为正式函数可调用性、归属 maps.renderer，并完整保留默认样式字典期望。
  原有地图类型/样式/HTML 数值及结构验证继续执行。

conftest 只把旧动态加载换成正式 SessionAgent fixture，并额外禁止旧导入。
OfflineLLM 构造参数断言、外部网络阻断/收尾断言仍在，未替换 GIS、Graph、随机森林或 Folium 为假业务对象。
ScriptedModel 的独立绑定对象仍能识别原模型/绑定模型接线错误。
七工具名称、完整参数集合、默认值、必填、实际绑定/分派正常路径和错误路径全部保留。

### 5. 前端与 Agent 集成

Test-verified：CSV/JSON/GeoJSON 上传、元数据建议、单次模型创建和绑定、同步/异步 Graph、
多步骤前后数据读取、随机森林、七工具、两会话隔离、同会话并发、原子失败和生命周期继续通过。
地图 Point/Line/Polygon/MultiPolygon、HeatMap/Choropleth、标题、样式、图层顺序及 HTML 内存下载通过。
没有修改前端用户流程、地图展示规则或删除策略。

### 6. Streamlit 启动脚本审核与最小加固

静态审核发现原脚本探测空闲端口后释放 socket，再启动服务，存在端口竞争窗口。
本机已安装 Streamlit 的服务器实现支持 port=0，并在真实 bind 完成后打印实际 URL。
据此仅调整验证设施：

- 使用 --server.port 0，由内核原子分配空闲端口，不抢占已有监听端口。
- 每次使用 UUID 独立日志，从自有服务成功绑定后的日志读取实际地址；保留存活检查。
- 显式显示启动 URL，不受 welcome message 隐藏配置影响。
- 启动等待 20 秒，单次健康请求 1 秒，首页和 WebSocket 连接 5 秒，页面执行 25 秒，连接关闭 2 秒。
- 仅终止自有 Popen 子进程，等待 5 秒；超时 kill 后再等待 5 秒回收，关闭日志及 WebSocket。
- Windows 监听 socket 可能晚于进程退出释放，增加最多 2 秒的端口关闭验证；不终止其他监听者。

新增三项日志地址识别测试、两项真实子进程正常/超时清理测试，**5 Passed**。
子进程测试的超时分支只注入 wait 超时，不伪造业务计算或应用成功结果；finally 也保障测试自身清理。

实际重新运行 .venv\Scripts\python.exe tests/check_startup.py：健康 HTTP 200 / ok，首页 HTTP 200，
真实 WebSocket 收到上传、聊天、地图组件及 FINISHED_SUCCESSFULLY，无异常元素。
子进程不继承真实 API Key；退出后自有进程已回收、监听端口已关闭，没有留下本次检查的服务。
另持有一个已监听端口执行检查，服务获得不同端口并成功完成；没有影响该既有监听者。

首次额外检查在 wait 返回后立即探测端口，观察到一次短暂仍可连接，因此未把它直接判作残留子进程。
系统监听检查随后为空；带上限的重查在约 0.281 秒内确认关闭，正式脚本现已纳入此关闭验证。
首次失败日志与重查证据均保留。Not Run：浏览器 JS、地图视觉、真实下载交互及外部模型/OSM。

### 7. Known Defect 状态

13 个 strict xfail 与 Phase 3D/3E 的组成及标记相同，strict=True 和 raises=AssertionError 均保留。
KD-SESSION 的原正确断言继续 Passed；KD-CSV-METADATA 继续 strict xfail；
GIS 参考数值、容差、边界/异常输入期望未放宽。没有新增缺陷、XPASS 或意外失败。

### 8. 实际命令与最终结果

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe -m pytest tests/test_app_integration.py -k startup -q -ra --tb=short
.venv\Scripts\python.exe tests/check_startup.py
git diff --check
```

| 执行范围 | Passed | strict xfail | 意外 Failed / XPASS |
|---|---:|---:|---:|
| 本次入口基线 216 项，25.82s | 203 | 13 | 0 / 0 |
| 新增启动设施测试 5 项，2.84s | 5 | 0 | 0 / 0 |
| 加固后全集 221 项，23.82s | 208 | 13 | 0 / 0 |
| 最终全集 221 项，23.96s | 208 | 13 | 0 / 0 |

最终完整 pytest 退出码 0；git diff --check 通过。Passed 增加仅来自五项直接相关测试。
启动脚本也实际退出码 0。依赖、CI、部署框架未新增或升级。

本地证据日志被忽略，不提交：

- tests/logs/phase3e-review-baseline.log
- tests/logs/phase3e-review-test-migration.log
- tests/logs/phase3e-review-startup-tests.log
- tests/logs/phase3e-review-startup.log
- tests/logs/phase3e-review-occupied-port.log（立即探测的失败）
- tests/logs/phase3e-review-occupied-port-recheck.log
- tests/logs/phase3e-freeze-run.log、phase3e-freeze-final-run.log
- tests/logs/phase3e-freeze-startup.log
- tests/logs/phase3e-startup-server-*.log（各自有服务日志）

### 9. 最终提交文件范围

沿用 Part IX 的完整 18 文件清单及归属，没有增加范围外文件。
Finalization 只进一步调整 tests/check_startup.py、tests/test_app_integration.py 和本报告。
所有候选均经源码/语法/行尾/敏感格式及本机路径检查；不包含 .env、虚拟环境、缓存或运行日志。
根 README 的架构、版本、安装、启动、配置、测试与外部迁移说明符合实际代码。

审核通过后仅暂存这 18 文件，检查 staged diff，创建独立本地提交：
refactor: complete layered architecture integration。
实际提交 Hash 和提交后 Git 状态由本轮最终答复及 git log 提供，避免把提交自身 Hash 写进自身内容。

### 10. Git 状态检查边界

提交前父基线固定为 be5fac920e01674d5227aff4b225d2b393d29e84，
main 固定比较 dcad948d029842a555c609149b5cc226a1fdfe90。
提交后再次核实分支、父提交、18 文件列表、工作区/暂存区和 main；不 Push、Merge、Rebase、Reset。

### 11. 最终验收范围

满足本阶段的分层结构、内部调用迁移、功能回归、隔离、独立性、测试迁移和启动验证要求，
具备创建独立本地基线的条件。13 项历史缺陷仍按 Known Defect 跟踪；真实服务理解、浏览器视觉、
未知外部脚本、生产负载和其他平台未验证，不能从本次自动化结果推断其通过。
不实施后续阶段，不修复无关历史缺陷或新增业务功能。
