# GeoChat Phase 4D — CRS 数据流修复

日期：2026-10-24。分支 `refactor/modular-architecture`；实施基线 HEAD `fa441f0181bac0ba70c5b85fd3a458bb08e5d699`。开始时仅有 Phase4C 的两个预期未跟踪文件，没有其他成员修改。本次没有 Commit、Push、Merge、Rebase 或 Reset。

标记：**Code-confirmed** 是源码/静态核对；**Test-verified** 是实际运行结果；**Known Defect** 是保留的历史缺陷；**Not Run** 表示未执行。浏览器中的模型回复为受控 Mock，不等于真实 DeepSeek 自然语言理解验收。

## Part I — CRS Fix Summary

**Test-verified：KD-CRS 已修复。** 输入源 CRS 现在经过 Dataset、Session View 和 Tool 适配传递，原始投影坐标保留；地图和HTML导出在前端边界使用真正转换到4326的副本。

- 完整 pytest：**295 Passed、11 strict xfail，共306项，0 Failed、0 XPASS，30.69s**。
- 新增 CRS pytest：**50项全部通过**，包含独立100m、1km²和点计数/密度参考。
- 独立真实 Chrome CRS 验收：**7 Passed、0 Failed**；真实上传、正式 Graph/ToolNode/GIS、实际 Leaflet DOM、页面HTML下载、两个独立会话及Reset。
- 39项诊断：**37 Reference Met、1 Known Defect、1 Inconclusive、0 Risk Reproduced、0 Diagnostic Error**。不是“39个Passed”。
- Phase4A 长流程本次曾出现时序超时和瓦片取消诊断失败，未被改写为Passed；证据及验证边界见 Part VIII。

## Part II — Changed Modules and Interfaces

| 文件/入口 | 实际修改 | 接口影响 |
|---|---|---|
| gis/data_io.py:27 resolve_source_crs | 识别原生/显式/格式默认CRS，检查冲突 | 新纯函数；不查询Session/模型 |
| gis/data_io.py:119 extract_features_to_gdf | 保留源CRS、支持原生GDF、复制可变属性，校验坐标 | 原位置参数保留；新增keyword-only source_crs；未提供与明确None不同 |
| gis/data_io.py:85、108 | 检查有限坐标、地理纬度及可转换性；transform_geodata真正调用to_crs | 新的CRS转换防护，不改变公式 |
| gis/data_io.py:158 prepare_map_layers | 创建WGS84渲染副本，保留属性、ID、样式与原载荷形状 | 新地图调用边界 |
| core/contracts.py:11、24、73 | CRS值规范化、声明检查和Dataset一致性校验 | Dataset/Layer/ToolResult字段及默认值未改变 |
| core/session.py:128 add_upload | 新增可选crs上下文；原生CRS/None保留 | 现有调用继续有效；冲突声明现在报错 |
| core/session.py:98、211 | raw/map视图及Dataset-ID直接解析均携带crs，包括None | 视图字典新增字段；原字段保留 |
| core/session.py:116、182、260 | 注册、旧视图导入和原子提交校验CRS；纠正旧显示层默认标签 | 不改ID、生命周期、锁和提交策略 |
| agent/tools.py:57 | 计算/显示工具使用输入的显式CRS；ML特征取WGS84副本 | 七工具名称、参数、默认值、注册顺序不变 |
| gis/geometry.py:13、24；gis/statistics.py:14、26 | 将原to_crs调用换成带验证的转换辅助函数 | 目标EPSG、Buffer算法、round、within、密度公式不变 |
| maps/renderer.py:17、45 | create_layered_map拒绝显式投影/未知的未准备wire数据 | WGS84旧调用兼容；投影视图调用需先prepare_map_layers |
| frontend.py:147、162 | 注册前确定源CRS，显式传给Session | 不变更元数据模型策略或注册顺序 |
| frontend.py:89、189 | 显示和导出均准备WGS84副本；CRS失败显示错误，不产生错误HTML下载 | 下载按钮、名称geogpt_map.html、MIME text/html保留 |

**Code-confirmed / Test-verified**：Core现在直接复用已安装的pyproj做CRS值解析/等价比较，不含投影计算，不导入gis、agent、maps、Streamlit。GIS仍不导入Core/Session/Agent/UI；Maps不导入GIS/Agent/Session。既有独立导入和依赖方向测试均通过；没有新增依赖或改requirements。

这比Phase4C纯结构校验的初步建议增加了Core层的CRS值验证，原因是仅比较字符串不能正确识别EPSG、URN等价标识，也无法可靠拒绝无效EPSG。现有pyproj已声明为直接依赖，无新增框架。

## Part III — CRS Source and Conflict Rules

**Code-confirmed / Test-verified**：

1. CSV解析保持原返回结构及lat/lon协议，WGS84；不推断任意x/y。JSON lat/lon记录同样按既有WGS84协议处理，普通属性字段crs/bbox不被当作GeoJSON声明或删除。
2. 支持的Feature、FeatureCollection及既有Feature列表，没有声明或显式上下文时使用WGS84协议；任意未知JSON结构不据此获得4326。x/y载荷在GIS入口明确报错；直接存入Session时只能保持crs=None。
3. 旧式named-crs支持可由现有PROJ识别的EPSG/OGC标识和URN，并要求地理或投影CRS。25832、4326和CRS84路径有测试。linked、未知、畸形及非支持类型（如地心4978）拒绝，不请求CRS链接。
4. 显式null或source_crs=None表示未知，GIS分析/地图准备报错，不回退4326。Session可保存显式未知，视图仍传递None；这不是可信的可分析输入。
5. 顶层、子级、原生对象及显式上下文的声明必须等价；忽略权威定义中的轴顺序差异来比较等价性，GeoJSON几何仍按x/y处理，不交换lon/lat。子Feature声明不能将默认WGS84的兄弟对象悄悄重标为投影坐标。
6. 原生GDF有CRS时复制并保留；无CRS时必须有明确可靠上下文。只在该无CRS副本上用set_crs声明上下文；所有坐标转换均用to_crs，没有allow_override。
7. Dataset.crs与data.crs或显式数据声明冲突时拒绝注册/提交。别名可等价；不是仅比较字符串。对象、索引、嵌套属性及源几何有不变性测试。
8. 同一分析CRS的no-op转换也不能掩盖无法得到有限地理位置的极端坐标；输入与转换结果都检查。没有引入自动UTM猜测、全球适用范围保证或拓扑修复。

**边界**：可信来源在此指存在一致、可解析的声明或明确格式协议，不意味着系统能证明上游声明符合真实世界。对数值看起来合理但标签实际错误的数据，不能凭坐标自动判断真实EPSG。

## Part IV — Session / Agent / Map Data Flow

```text
上传原载荷 → 解析 → 确定源CRS → 原有Mock/真实元数据建议入口
           → add_upload(crs=源CRS) → Dataset保存源坐标
           → raw/map View及resolve_in显式带crs
           → Tool适配 → 带真实CRS的GDF → 原有GIS计算
           → 原子提交派生Dataset和Layer（含源引用）
           → prepare_map_layers → WGS84副本 → Renderer / 内存HTML
```

**Test-verified**：25832 Session工具路径得到100m；1km²面与25832/4326混合点层得到计数1、密度1。Buffer生成非空4326数据；Area/Density保持输入源CRS。来源Dataset保持原坐标和CRS，派生parent_ids正确。

Feature-list形式可能没有集合级crs，但Session视图的独立字段仍在。Dataset-ID直接查找也传递CRS，不依赖图层显示状态。旧视图导入保留原生CRS，手工Dataset的错误默认标签会明确报错。

**Test-verified**：未知CRS、矛盾Dataset或坐标转换失败不会留下新的Dataset/Layer；直接pending view冲突也在发布前失败。既有原子性和会话并发测试未修改，全部通过。

**Code-confirmed**：Renderer不查询Session。地图副本保持ID、名称、样式、Feature属性及几何类型；移除与转换后坐标不一致的CRS/bbox运输头，原数据不被修改。默认中心、最后图层bounds策略、图层顺序、色带与LayerControl代码未改变。

## Part V — GIS Correctness Tests

新增 tests/test_crs_flow.py 共50个参数化收集项，均为Passed：

| 验证内容 | 主要测试 |
|---|---|
| 已知UTM坐标→(9,51)、原坐标保留 | test_known_utm_source_and_actual_wgs_transform（4种载荷） |
| 识别/拒绝旧声明、null、畸形、冲突 | test_supported_named_headers、test_unsupported_or_unknown_declaration_never_falls_back、test_conflicting_headers_and_context_rejected_without_mutation |
| 子级不能重标兄弟数据；x/y不能假设4326 | test_child_declaration_cannot_silently_relabel_wgs_sibling、test_arbitrary_xy_payload_is_not_assigned_wgs84 |
| 原生未知CRS、可靠上下文 | test_unknown_native_frame_and_reliable_explicit_context |
| 100m、Session View、三种运输形式 | test_session_crs_transport_and_analytic_tools |
| 1km²、计数1、密度1、混合CRS | test_area_density_mixed_crs_matches_independent_square |
| 地图真实转换、ID、bounds、HTML | test_wgs_map_copy_uses_correct_position_and_retains_source |
| Line/Polygon/MultiPolygon地图 | test_projected_line_polygon_multipolygon_map |
| 属性、嵌套所有权、样式、Feature ID | test_native_frame_nested_attributes_not_shared_after_conversion、test_point_protocol_properties_and_projected_feature_attributes_survive |
| 注册/旧入口/原子提交冲突 | test_dataset_conflicts_rejected_and_legacy_display_infers_real_crs、test_pending_view_crs_conflict_does_not_partially_publish |
| 未知CRS五工具失败、无状态污染 | test_unknown_crs_tool_failure_does_not_publish |
| 错标经纬度、极端投影坐标不报告成功 | test_wrong_coordinate_interpretation_cannot_succeed |
| 同步/异步Graph读取派生数据 | test_multistep_graph_consumes_derived_crs |
| 两个Session并行计算/Reset隔离 | test_projected_and_geographic_sessions_remain_isolated |
| RF特征保持WGS84及源数据不变 | test_random_forest_uses_wgs_features_and_preserves_projected_source |
| 真实frontend AppTest上传/显示和错误反馈 | test_real_frontend_projected_upload_analysis_and_render、test_frontend_bad_crs_is_visible_error_not_dataset |

独立参考来自构造事实：25832两点相差100m；1000m×1000m方形为1km²；严格内部一点计数1。不是用业务函数的输出生成参考。原KD-CRS固定常数参考和(9,51)断言保留。

诊断前后：

| 39项诊断结果 | Phase4C原证据 | Phase4D |
|---|---:|---:|
| Reference Met | 14 | 37 |
| Known Defect复现 | 3 | 1 |
| Risk Reproduced | 21 | 0 |
| Inconclusive | 1 | 1 |
| Diagnostic Error | 0 | 0 |

诊断脚本按新增公开边界显式传CRS和准备地图；原生GDF接受/拒绝的旧行为刻画更新为新接口要求；错误Dataset被拒绝计为安全结果。未修改独立数值期望。D12仍直接检查Folium原生GDF分支，未用准备后的数据冒充该分支。D04包含后续GDF验证，所以非法纬度会被挡住；这不等于CSV解析器的KD-COORDINATES已修复，原strict xfail仍失败。

原证据 tests/logs/phase4c_crs_diagnostics.json 保留，SHA256仍为 `66b8e1a9beae64cc88730e563492b11cec78cb348d63499b80478456ae2cae9a`。新证据 tests/logs/phase4d_crs_diagnostics.json；脚本默认输出现改为Phase4D文件，避免覆盖Phase4C证据。

## Part VI — Compatibility and Full Regression Results

实际最终命令：

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe tests/diagnose_crs.py --evidence-name phase4d_crs_diagnostics.json
.venv\Scripts\python.exe tests/phase4d_browser.py
git diff --check
```

pytest实际使用Tee-Object保存 tests/logs/phase4d-pytest.txt 并保留pytest退出码。最终 **295 Passed、11 xfailed，共306项，30.69秒，退出0，0 Failed、0 XPASS**。306=原256+新增50；原KD-CRS从xfail转为Passed。

**Test-verified**：七工具注册、Schema、绑定调用、CSV/JSON/GeoJSON、所有既有地图类型、内存HTML、RF、Session/原子性、GIS独立参考和模块独立性回归通过。

**Code-confirmed**：对HEAD和工作区AST比较，七个@tool函数的参数/默认值完全一致；原KD-CRS测试body完全一致，仅删除该装饰器。Graph、LLM、conftest、其他Known Defect测试、依赖文件没有diff。

调试期间的失败事实：

- 初轮完整旧测试：244 Passed、11 xfail、1 strict XPASS，正是已修复的KD-CRS；随后经过新测试和数值验证才移除标记。
- 新测试首轮4 Failed：三个是把德文beieinander中的nan字串误判为NaN，已改为独立单词检查，100m数值断言保留；另一个暴露极端投影坐标在同CRS分析时跳过有效性检查，已补充真实地理可转换性防护，正确错误期望保留。
- 后续完整回归无意外失败。浏览器调试失败没有删除，见下一节。

## Part VII — Known Defect Changes

| 项目 | 最终状态 |
|---|---|
| KD-CRS | **Test-verified：修复，原断言Passed，仅移除对应strict xfail** |
| KD-BUFFER（51N） | **Known Defect，strict xfail保留**；正确源CRS下500m参数仍约314.45–315.30m地面半径 |
| KD-TINY、KD-DENSITY | **Known Defect，strict xfail保留**；公式、提前round和零面积处理不变 |
| KD-GEOMETRY三项 | **Known Defect，strict xfail保留**；没有普遍拓扑/空几何修复 |
| KD-LOOKUP、KD-MISSING-POINTS、KD-WEIGHT、KD-FEATURE、KD-COORDINATES | **Known Defect，strict xfail保留** |
| KD-CSV-METADATA、KD-SESSION | 原已修复行为的回归通过，本次未调整原测试 |

总数12→11，只因KD-CRS真实修复。没有把Buffer误差称为本次修复，也没有扩大CSV解析范围检查或修改within的边界/重叠语义。

## Part VIII — Browser Verification

**Test-verified：最终独立脚本 tests/phase4d_browser.py 退出0，7 Passed、0 Failed。** Chrome 156.0.8078.47，真实Streamlit服务，测试入口只隔离凭据/Mock LLM和外部OSM回复；正式frontend、Graph、ToolNode、Session、GIS和Renderer均执行真实代码。

最终证据目录：`tests/logs/phase4d-browser-3b42a79db527430f948be1c0dafb9b18/`。

| 验收项 | 状态与证据 |
|---|---|
| 两个独立BrowserContext实际上传 | **Passed**：投影GeoJSON和标准JSON分别上传，不同业务session_id；源CRS25832与坐标(500000,5649824.888)保留 |
| 聊天进入真实Graph/ToolNode | **Passed**：显示、100m距离、50m Buffer、派生面面积连续执行；4个ToolResult成功，原Dataset与另一会话不变 |
| 地图真实DOM与坐标 | **Passed**：3个Marker、6条SVG路径、Marker都在map bounds内；首点约(50.9999999988,9)，见map-dom.json、marker-positions.json、crs-map.png |
| 实际HTML页面下载 | **Passed**：新Chrome下载事件完成；geogpt_map.html，45800字节，HTTP200、text/html、Content-Disposition文件名正确；3个WGS84 Marker及派生GeoJSON；没有map_export.html |
| Reset隔离 | **Passed**：仅A清空，B数据/CRS不变 |
| 页面异常 | **Passed**：无Python exception元素、未捕获JS异常或console error |
| 清理 | **Passed**：自有Chrome/Streamlit进程回收、两个端口关闭；cleanup.json记录无清理错误 |

截图已抽查，显示3个点、缓冲面及专题图叠加层。该场景通过现有UI开关关闭底图，验证的是CRS叠加层，不宣称验收了在线瓦片或打开导出HTML后的完整多图层视觉效果。下载使用实际页面按钮；DOM观察器只避免漏掉现有短暂下载控件，没有直接调用序列化函数代替下载。

**保留的浏览器失败/限制事实**：

- 本次曾将CRS场景加入Phase4A长流程，遇到Chrome初始化、聊天/导出重绘时序超时。最完整的一轮目录 `tests/logs/phase4a-a7f1620c15844f7d98c8a0a7ed8a21c2/` 为 **27 Passed、1 Failed、2 Not Run**：4个CRS功能场景通过，但BROWSER-EXCEPTIONS因11个tile.openstreetmap.org请求ERR_ABORTED/canceled=true失败；Python/JS/console异常均0。该失败没有放宽断言改为通过。
- 其他长流程失败证据保留于9f1acaa2、eb2fb305、5e866c1e、8cc597d4、e8e2a7ed、70487aad对应的完整phase4a日志目录。均验证了资源清理。本报告不声称本次Phase4A完整长流程最终全绿。
- 最终Phase4A文件只增加投影fixture和first_geometry观察字段；AST核对main、CDP和全部交互/断言helper与HEAD完全相同，没有删除旧检查。最终CRS验收独立执行，避免长流程互相影响。
- 独立脚本首次7项通过后增加DOM数量与bounds检查；一次因AwesomeMarkers选择器不适配超时（phase4d-browser-42ebbd2629fc46b48263c5b9bfc60442，3 Passed、1 Failed），改用Phase4A已验证的marker-pane实际子元素选择器，保留3个Marker、6条路径和bounds断言后重新通过。
- 最终独立运行仍记录2个ERR_ABORTED/canceled=true网络事件及21条warning，全部保留browser-events.json。其Passed页面异常项只指Python/JS/console，并非“零网络事件”。这些不计作已修复的GIS缺陷。

**Not Run**：真实收费DeepSeek、在线Nominatim、全球CRS/大数据性能、下载后完整多图层人工视觉验收。没有为本次验收调用真实收费模型。

## Part IX — Git Diff and Modified Files

**8个生产文件**：

- gis/data_io.py、gis/geometry.py、gis/statistics.py：源CRS和转换防护。
- core/contracts.py、core/session.py：CRS一致性和运输字段，不改变业务状态模型。
- agent/tools.py：输入适配和RF的WGS84特征副本。
- frontend.py：上传CRS声明、显示和导出副本及错误反馈。
- maps/renderer.py：拒绝未准备的显式非WGS84/未知运输输入，样式算法不变。

**既有测试文件**：tests/test_gis_correctness.py仅移除KD-CRS装饰器；tests/phase4a_acceptance.py只加fixture和观察数据。**新增**tests/test_crs_flow.py、tests/phase4d_browser.py、本报告。**更新既有未跟踪文件**tests/diagnose_crs.py以使用新边界及独立输出路径。**保留未改**tests/PHASE4C_REPORT.md。

报告加入前tracked diff为10文件、360新增/53删除；Git未计入未跟踪脚本和报告。最终工作区应为10个已跟踪修改、5个预期未跟踪文件，共15项。所有日志、截图、下载、浏览器profile和测试缓存都在既有忽略规则内，没有新增密钥、环境文件或依赖。

**Test-verified**：git diff --check通过；暂存区为空；分支/HEAD不变；main仍是 `dcad948d029842a555c609149b5cc226a1fdfe90`；backend.py不存在；没有Git提交或远端操作。frontend.py原ReadOnly属性已恢复。工作区按本阶段要求保留待审核修改，不是干净状态。

## Part X — Remaining Risks and Acceptance Assessment

**CRS数据流修复已通过自动化与独立真实浏览器的本阶段验收，可以进入审核；尚未提交。** Phase4A完整长流程的时序/网络诊断失败仍需单独看待，不能用本次独立7项通过替代整套Phase4A全绿声明。

仍需知道：

1. 一致、可解析的声明不能证明真实来源；合理范围内的错误标签无法自动辨认。
2. 固定25832分析投影不具备全球精度保证，3857 Buffer也仍非地面米；本次不改选投影策略、公式或精度。
3. 地图、坐标验证及防御性副本增加转换/内存开销，尚未做大数据性能测试。
4. 外部调用非WGS84 Session View时须显式使用prepare_map_layers再交create_layered_map；标准WGS84旧调用仍兼容。不能用set_crs把已有投影载荷改标签蒙混过关。
5. 通用空/无效几何、极小面积和密度问题仍存在；本次保护针对CRS解释和转换错误，不是全局数学/拓扑修复。

本阶段到此停止，不继续其他缺陷修复或新功能，不Commit、Push或Merge。

## Phase 4D Finalization — 独立审核与复测（2026-10-24）

本节为最终审核结果，更新上文实施时的数量和待调查状态；上文保留为实施记录。审核开始确认分支为 `refactor/modular-architecture`，HEAD 为 `fa441f0181bac0ba70c5b85fd3a458bb08e5d699`，main 为 `dcad948d029842a555c609149b5cc226a1fdfe90`。实际为10个tracked修改、5个预期untracked文件，暂存区为空，没有来源不明文件。Phase4C报告作为本次CRS修复的审查依据一并归档，不属于新开发范围。

### 1. 生产代码、接口和断言审核

**Code-confirmed**：逐项审查全部生产diff、三个新增脚本/测试及两份报告；本次Finalization没有再修改生产代码。

- CSV/JSON的lat/lon协议及标准GeoJSON使用WGS84；不推断未知x/y。旧named声明仅接受可解析的地理/投影CRS，null、linked、矛盾及不支持声明不能静默回退4326。
- 原生GeoDataFrame保留CRS；未知CRS只有在调用方提供可靠上下文时才在副本上声明。唯一set_crs位于该分支，坐标变换实际调用to_crs，没有allow_override。
- Dataset注册和pending发布校验Dataset/data的CRS一致性；Session的raw/map视图、Dataset-ID查询显式携带crs，包括None。复制和锁策略保持不变。
- Tool输入取Session的源CRS；Buffer返回4326，面积/密度保留源CRS，RF使用WGS84特征副本。CRS解释/变换失败在发布前反馈，不把错误坐标产生的非有限结果当作成功。
- frontend显示和HTML导出使用同一prepare_map_layers边界；原Dataset不投影。Renderer仅校验WGS84运输输入，不查Session。bounds、样式、顺序、标题、文件名、MIME逻辑未改变。
- AST对照HEAD：七个@tool函数的名称、参数、注解、默认值及源码顺序完全一致；未把source_crs/crs参数加入工具Schema。既有工具绑定、Schema和真实Mock-Graph调用回归通过。agent/graph.py、agent/llm.py、conftest及其他Known Defect文件无diff。
- GIS/Core/Maps没有新增反向依赖；Core只复用现有pyproj做声明验证，GIS不导入Agent/UI，Maps不导入GIS/Agent/Session。没有新增依赖、全局业务状态或旧backend门面。
- KD-CRS的函数body和独立(9,51)参考断言与HEAD完全相同，只移除了对应strict xfail。其他11项strict xfail的独立断言、投影目标、round、within、面积和密度公式没有被改写。

**Test-verified**：100m、1km²、区域计数1/密度1、源对象和嵌套属性不变、同步/异步派生数据、并发Session隔离、未知/矛盾CRS失败无状态发布、RF及已有格式/地图类型均通过。额外增加3项重复渲染测试，覆盖Collection、Feature列表和GeoDataFrame：准备两次结果一致，坐标保持(9,51)，ID和样式不变，原25832输入不变。

公共接口变化限于可选keyword-only CRS上下文、视图新增crs字段和新的准备/验证函数。外部脚本若直接将非WGS84视图交Renderer，应先调用prepare_map_layers；不能依靠改标签代替转换。合法WGS84既有调用保持兼容。

### 2. 浏览器失败的证据与分类

**Test-verified / Code-confirmed**：原完整失败日志 `tests/logs/phase4a-a7f1620c15844f7d98c8a0a7ed8a21c2/cdp-events.json` 的11条失败全部是同一个Folium frame的OSM PNG重复请求（type=Other、ERR_ABORTED、canceled=true）。相同session/frame/URL的Image请求已HTTP200、image/png并loadingFinished，约40ms后重复请求被取消。该frame的销毁事件明显更晚，因此不能把原11条直接归因为frame销毁；CDP没有提供足够的JS调用栈来判定具体Leaflet函数。成功瓦片交付、地图截图及点/面DOM和交互断言支持“重复请求被取消，非瓦片未取得”的结论，没有发现GIS或地图功能回归。原失败记录仍保持27 Passed、1 Failed、2 Not Run，没有改写历史结果。

仅对测试基础设施作以下最小调整：

1. 新增classify_network_failures，只有本地Folium frame、精确OSM PNG路径、同session/frame/URL已经完成200 PNG交付、紧接着的Other重复请求在250ms内取消且自身没有响应时，才单独记录为已加载重复瓦片取消。未知来源、超时、Script、GeoJSON、普通Image失败、不同frame、HTTP错误证据、缺少完成记录等均保留为失败，不笼统忽略aborted/cancelled。
2. 新增17项离线测试：1项正例（含输入不变断言）及16项拒绝分类反例；原日志重放精确分类11条、未解释失败0条。重放证据见 `tests/logs/phase4d-finalization-network-replay.json`。原日志SHA256为 `8ba8b429baa80d7ad2862a4672111c3bcb3b802bd5bcdaa89f28244b30736d9a`。
3. Finalization首轮长流程在错误工具反馈步骤超时，目录 `tests/logs/phase4a-a01b159636444751aaf58ee6c6554d2e/`，14 Passed、1 Failed。失败DOM/Session没有收到该次absent命令，未出现业务Python异常，输入处于地图重跑交互时序。聊天helper现在等真实Streamlit状态notRunning稳定1秒、确认输入控件启用/焦点及完整文本后才发送；不重试工具、不伪造回复、不删除原业务断言。

复测记录（所有失败证据仍保留）：

| 运行 | 实际结果 | 本地证据目录 |
|---|---|---|
| 原实施长流程，曾含4个CRS扩展场景 | 27 Passed、1 Failed、2 Not Run；11个取消重复瓦片被旧规则直接记失败 | tests/logs/phase4a-a7f1620c15844f7d98c8a0a7ed8a21c2/ |
| Finalization首轮长流程 | 14 Passed、1 Failed；聊天输入时序超时；已清理进程 | tests/logs/phase4a-a01b159636444751aaf58ee6c6554d2e/ |
| 最小测试修正后完整Phase4A长流程 | **24 Passed、0 Failed、2 Not Run**；原24项全部完成；0 Python/JS/console错误、0网络失败，0需要分类的重复取消 | tests/logs/phase4a-57aae90b24da4ea6bb599a7aacdb9e3b/ |
| Finalization独立CRS真实Chrome | **7 Passed、0 Failed**；上传、Graph、100m、Buffer/派生面积、3个Marker/6条SVG、实际HTML下载、Reset隔离及清理 | tests/logs/phase4d-browser-df76ab12154846f986c0b691231306cf/ |

24项与旧27项的计数不同，是因为CRS扩展从临时长流程移至独立7项脚本；不是删除原Phase4A功能检查。最终长流程main除网络分类调用外，业务场景/断言保持；helper只增加输入就绪和文本断言。随机森林、七工具、多轮、失败反馈、LayerControl/样式、CSV/JSON/GeoJSON、真实下载及两个BrowserContext全部执行正式业务代码；LLM/OSM回复受控Mock。

本轮CRS专项原始事件仍有2个Script类型ERR_ABORTED（不是上述重复瓦片）。每条取消后紧接对应旧iframe的Page.frameDetached(reason=remove)，新iframe随后成功取得同一个leaflet.js并完成真实地图DOM检查，符合正常组件重建的证据。保留这些网络事件，未套用瓦片规则或宣称专项“零网络事件”。长流程新规则仍会把Script取消列为未解释失败；本轮长流程没有此类事件。自然发生的取消时机不稳定，未声称每次运行都能复现11条；原始CDP重放可重复。

最终长流程37条warning（10组）保留：24条iframe不支持的feature提示、10条allow-scripts/allow-same-origin的sandbox安全配置提示、3条Canvas读回性能提示。它们不是GIS数值失败；sandbox提示涉及现有依赖嵌入方式，不能仅因无console error就认为没有风险，本阶段没有修改安全配置。CRS专项截图已抽查，3个点/缓冲面位于相应位置；该场景关闭底图，不作为在线瓦片服务完整可用性保证。

### 3. 最终测试命令、计数与已知缺陷

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe tests/diagnose_crs.py
.venv\Scripts\python.exe tests/phase4a_acceptance.py
.venv\Scripts\python.exe tests/phase4d_browser.py
.venv\Scripts\python.exe -m pytest tests/test_browser_network.py tests/test_crs_flow.py -q --tb=short
git diff --check
```

**Test-verified**：审核最初完整pytest为295 Passed、11 xfailed（31.93s）；新增测试后最终为 **315 Passed、11 strict xfail，共326项，35.44s，0 Failed、0 XPASS，退出0**。增加20项=3项重复投影+17项网络分类；不是为匹配旧数字修改断言。最终完整命令通过Tee-Object保留 `tests/logs/phase4d-finalization-pytest.txt` 并显式返回pytest退出码。两个相关文件单独运行70 Passed（5.16s）。

CRS诊断仍为 **39项：37 Reference Met、1 Known Defect、1 Inconclusive、0 Risk Reproduced、0 Diagnostic Error**；`tests/logs/phase4d_crs_diagnostics.json`。原Phase4C证据未覆盖。诊断不计入pytest数量。地图截图、下载、完整CDP日志及cleanup检查均在上述忽略目录；长流程3个端口均关闭，CRS专项2个端口关闭，自有子进程均回收，无清理错误。

**Known Defect**：12→11只因为KD-CRS真实修复；3857 Buffer地面误差、极小面积归零/密度精度、3项无效/空/零面积几何、图层查找、缺失点层、零线宽、单Feature点渲染及CSV范围校验缺陷全部保持strict xfail。KD-SESSION、KD-CSV-METADATA既有修复未退化。

**Not Run**：真实DeepSeek推理、在线Nominatim、下载HTML重新打开后的完整人工多图层视觉验收、全球CRS精度及大数据性能。当前标签一致性/可转换性无法证明来源声明一定真实；固定25832/3857策略依旧有限制。本次没有改变无Key上传政策。

### 4. 提交范围与最终判断

最终拟提交16项，全部属于CRS修复、直接验收基础设施或其审查证据：

| 文件 | 归属 |
|---|---|
| agent/tools.py | CRS输入适配及RF经纬度副本 |
| core/contracts.py | CRS声明和Dataset一致性校验 |
| core/session.py | 源CRS/None贯穿视图、查询和原子发布 |
| frontend.py | 上传声明、显示/HTML的WGS84准备和错误反馈 |
| gis/data_io.py | CRS解析、冲突/坐标验证、真正转换和渲染副本 |
| gis/geometry.py | 复用带CRS验证的原目标投影 |
| gis/statistics.py | 复用带CRS验证的原统计投影 |
| maps/renderer.py | 拒绝显式非WGS84/未知运输输入 |
| tests/phase4a_acceptance.py | UTM fixture/观察字段、输入就绪及精确网络分类 |
| tests/test_gis_correctness.py | 保留KD-CRS原断言，移除已修复xfail |
| tests/test_crs_flow.py | 53项CRS直接/集成/不变性回归 |
| tests/test_browser_network.py | 17项网络分类正反例 |
| tests/phase4d_browser.py | 7项真实Chrome CRS专项验收 |
| tests/diagnose_crs.py | 重复验证Phase4C参考及修复效果 |
| tests/PHASE4C_REPORT.md | 修复前独立审查、根因和决策依据 |
| tests/PHASE4D_REPORT.md | 实施及本次最终审核/失败调查记录 |

日志、截图、浏览器profile、下载、缓存和环境文件保持忽略，不提交。代码/测试没有真实Key、个人路径或新依赖。backend.py不存在。最终审核认为可按授权创建单独本地提交；确切Commit Hash和提交后Git状态以本次最终回复及Git对象为准，避免报告自引用提交Hash。只完成Phase4D，不开展后续开发或远端操作。
