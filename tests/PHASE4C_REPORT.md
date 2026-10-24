# Phase 4C — CRS 正确性审查与最小修复方案

日期：2026-10-24。仅审查、离线诊断和方案设计；没有修改生产代码、既有测试或 xfail，没有实施 CRS 修复，没有 Git 提交或远端操作。

标记：**Code-confirmed** 为当前源码/安装库的静态证据；**Test-verified** 为本次执行证据；**Known Defect** 为已有缺陷；**Reasonable Inference** 为尚未经端到端实测的影响推断；**Decision Required** 为实施前须批准的策略；**Not Run** 为本次未执行。

## Part I — Current CRS Handling

**Code-confirmed / Test-verified**：开始时工作区干净，分支 `refactor/modular-architecture`，HEAD `fa441f0181bac0ba70c5b85fd3a458bb08e5d699`；main 为 `dcad948d029842a555c609149b5cc226a1fdfe90`。根目录没有 backend.py，Git 也未跟踪它。未找到适用的 AGENTS.md。复用项目 .venv，没有安装依赖。

环境：Windows、CPython 3.14.5，解释器为项目相对路径 `.venv/Scripts/python.exe`；GeoPandas 1.2.0、pyproj 3.8.0、Shapely 2.2.0、Folium 0.20.0。

| 当前入口 | CRS 来源及实际行为 | 证据 |
|---|---|---|
| CSV | 仅支持精确列名 lat/lon；按度数解释为 WGS84，无 CRS 列协议、坐标范围验证或 x/y 支持；额外 crs 列只是属性 | gis/data_io.py:57、70；D04 |
| JSON 点字典列表 | lat/lon 生成坐标 [lon,lat]；固定声明 4326 | gis/data_io.py:20、25、40；D21 |
| GeoJSON / JSON Feature、FeatureCollection、Feature 列表 | 几何及属性被读取；顶层或子层 crs 不参与解析；统一声明 4326，不转换坐标 | gis/data_io.py:28、40、63；D02、D03 |
| 裸 GeoJSON Geometry | 不是当前 extract_features_to_gdf 支持的输入，不应把扩展该格式列为本次修复 | gis/data_io.py:20–40 |
| GeoDataFrame → 转换器 | extract_features_to_gdf 不接受 GeoDataFrame，报 ValueError；没有原地覆盖该对象 | gis/data_io.py:20–40；D05 三项 |
| GeoDataFrame → GIS Core | 已支持；按传入 .crs 真正执行 to_crs；无 CRS 时拒绝计算 | gis/geometry.py:17、30；gis/statistics.py:19、30；D06、D07 |
| GeoDataFrame → Session | add_upload 保留真实 .crs 或 None，也复制输入；但随后视图序列化/工具重解析不能保持这一事实 | core/session.py:79、90、125；D08 |

全生产 Python 文件搜索没有发现 `set_crs` 或 `allow_override` 调用。缺陷的实际位置是 `GeoDataFrame.from_features(..., crs="EPSG:4326")`：**重新构造对象时强制声明坐标系**，并非已有 GeoDataFrame 被 set_crs 原地修改。`set_crs`/构造器 CRS 参数仅声明坐标含义；`to_crs` 才转换坐标；当前下游 to_crs 用法不能修复已经错误的源声明。

**格式依据**：RFC 7946 §4 规定 GeoJSON 使用 WGS84 十进制度，坐标顺序是经度、纬度（OGC:CRS84），不再采用 2008 规范中的可替换 CRS 机制。因此“未写 crs 的合规 GeoJSON”有格式依据，不能与“任意未知坐标没有 CRS”混为一谈。[RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946#section-4)

**Code-confirmed**：2008 旧格式曾支持 named/linked crs；显式 `crs:null` 表示不能假定 CRS。本项目现在忽略这些声明。对旧投影 GeoJSON 的兼容应作为明确策略，而不是称为 RFC 7946 默认支持。[旧 GeoJSON 规范 §3](https://geojson.org/geojson-spec.html#coordinate-reference-system-objects)

## Part II — CRS Data Flow

1. **上传与解析**：frontend.py:131 只接受 CSV/JSON/GeoJSON。:145 调用 parse_uploaded_file；返回原始字典/列表，没有确定 CRS，也没有构造 GeoDataFrame。
2. **元数据建议**：frontend.py:149–156 先采样再调用 LLM。agent/llm.py:39–41 的集合采样不携带顶层 crs；但 raw_data 本身没有被该采样改写。LLM 只推荐名称、展示类型、字段，不能作为 CRS 推断或认证来源。本次未调用真实模型。
3. **Dataset 注册**：frontend.py:159 → core/session.py:125。GeoDataFrame 的 .crs 被保留；字典/列表一律标记 Dataset.crs=4326，包括嵌有 25832 声明的字典（D18）。Dataset 默认 crs=4326（core/contracts.py:15）；add_dataset 没有检查 Dataset.crs 与 data.crs 一致性（:114，D09）。仅显示层的旧格式导入也走默认 4326（:182，D10）。
4. **Dataset → 工具/地图视图**：core/session.py:90 调用 data.to_json()，未传 to_wgs84=True；当前安装版本默认 False。投影 GeoDataFrame 通常输出原坐标及旧式 named-crs，而非 WGS84 坐标。metadata.format="features" 时 :94 只取 features，会丢失集合级 CRS（D19）。raw/shown 字典 :103–107、resolve_in 的 :208–209 没有传独立的 Dataset.crs。
5. **Agent Tool**：agent/tools.py:148、177、186、220、294 只把 target["data"] 交给固定 4326 的转换器。即使 Dataset 保存了正确 25832，工具仍会误解坐标。可视化工具 :119–128 仅建立 Layer 引用，不转换坐标。
6. **GIS 计算**：先按错误/正确的 frame.crs 再执行 to_crs。正确源 CRS 的直接调用有效；错误源 CRS 无法被下游自动纠正。
7. **派生 Dataset / Layer**：core/session.py:245–295 原子发布，GeoDataFrame 的 .crs 记录在 :261；普通 JSON 输出仍默认 4326（:264）。Buffer 输出 4326，Area/Density 输出保留计算输入的 CRS。这里的“保持”可能只是保持错误标签；序列化可通过不等于空间结果正确。parent_ids 能记录来源，但不能修复错误几何。
8. **地图 / HTML**：frontend.py:188 与 :89 都把 Session.map_view 直接交给 maps.renderer。字典/列表路径不读取 CRS；GeoJSON/Choropleth 经 Folium 接收原字典，Point/HeatMap 直接用 [y,x] 作为纬经度。HTML 仍包含同一坐标，不额外修正。

**Test-verified / Code-confirmed**：GeoPandas to_json 的“不自动转 WGS84”行为已检查当前安装源码，并在 D08 中证实；公开 API 文档也说明默认 `to_wgs84=False`。[GeoPandas to_json](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.to_json.html)

## Part III — Root Cause of CRS Known Defects

**Known Defect / Test-verified：KD-CRS 仍存在。** 原 strict xfail 在 tests/test_gis_correctness.py:50，保留其独立正确性断言。本次 D02 对相同场景复现：已声明 25832 的 `(500000, 5649824.888131557)` 被标为 4326，随后 to_crs(4326) 不进行转换；参考位置为 `(9,51)`。

三个相连的缺口：

- 输入转换器忽略原 CRS，强制赋予 4326。
- Dataset 对字典的默认声明、手工 Dataset 默认值，以及旧显示层导入，可能与数据真实 CRS 冲突；现有元数据不是已验证的 CRS 契约。
- Session 视图未传递独立 CRS，部分 feature-list 运输格式甚至去掉集合声明；工具和地图各自继续假定经纬度。

**Test-verified**：不是所有错误都会报异常。D13 中四种投影输入工具均报告 success：Buffer 提交空 Polygon、Distance 返回 nan m、Area 提交 NaN 面积、Density 返回计数 0/密度 0。这些是现有缺陷沿真实适配/提交链路的表现；本次不把它们新增为 pytest xfail，也不宣称已修复。

**Code-confirmed**：现有解析器没有来源、显式未知、格式默认、可信声明之间的区分。尤其“None 作为已知未知”在运输后可能变成“没有字段，因此默认 4326”。D14 无 CRS GeoDataFrame 的 Session 工具路径报告约 70.2 m；这只能证明静默假设发生，**不能证明真实距离应是 70.2 m**。

## Part IV — Effects on GIS Operations and Maps

| 功能 | 当前转换与单位 | CRS 错误的实际影响 / 独立遗留风险 |
|---|---|---|
| Buffer | 源 → 3857，平面米 Buffer → 4326；gis/geometry.py:17–19 | D13 投影输入变成空缓冲且成功提交；独立 KD-BUFFER 在 51N 即使源正确，500m 参数只有约 314.45–315.30m 地面半径（D15） |
| Distance | 源 → 25832，几何最短距离，米，四舍五入 0.1m；:30–37 | 同一原生 100m 线段：直接 Core 为100m；经 Session/Tool 为 nan；不是质心距离问题 |
| Area | 源 → 25832，m²/1e6 得 km²，保留4位；gis/statistics.py:19–20 | 1km² 方形直接正确，经错误声明后为 NaN；结果几何仍保留输入 CRS |
| Point Count / PIP | 两侧分别 → 3857，严格 within；:30–35 | D06 直接混合正确 CRS 计数1；D20 地理面+投影点经 Tool 却计0；边界排除和重叠各计规则保持，未修改 |
| Density | 已舍入 area_km2 + 点数，点/km²，保留2位；:45–49 | CRS 可同时破坏分子和分母；D20 面积1仍计数0/密度0；极小面积归零、提前舍入是另外的 Known Defect |
| GeoJSON / Choropleth | maps/renderer.py:139、151 把字典直接交 Folium | D11 坐标仍为 `(500000,5649824.888…)`，并把它们用于 bounds |
| Point / HeatMap | :71–79、92–98，直接取 y 为纬度、x 为经度 | D11 Marker/HeatMap 数据同样错误，未做 CRS 转换 |
| 地图中心 / bounds | 构造中心固定德国；:52–57 按显式 view_updates、最后图层 bounds、回退 bounds 顺序选择 | 默认构造中心没被 CRS 代码更改；错误数据污染 fit_bounds，可能改变实际视窗；浏览器画面影响为 Reasonable Inference |
| 派生 Dataset 位置 | Buffer 输出4326，Area/Density 保留输入坐标及 CRS | 错误输入经运算后不能凭输出 CRS 标签恢复位置；原始上传对象和派生对象的所有权隔离不等于空间正确 |

**重要例外，Test-verified**：D12 直接把原生 25832 GeoDataFrame 交 add_geojson_layer 时，Folium 0.20.0 的 `GeoJson.process_data` 在 __geo_interface__ 分支会调用 to_crs(4326)，实际位置正确。其字典分支直接返回原字典，因此**这个库行为无法保护目前 Session → 字典 → Renderer 的主要路径**。不能笼统断言 Folium 对所有输入都不转换。

**Code-confirmed / Reasonable Inference**：25832 是 ETRS89 / UTM 32N、平面米，当前安装 PROJ 数据库给出的适用区域约东经6–12度的欧洲。正确源 CRS 不等于固定25832适用于全球精确距离/面积；3857 的适用纬度也有限。这些分析 CRS 的选取策略需另案，不能混入本次源 CRS 修复。ML 工具 :221–222 直接取 geometry.x/y 为 lon/lat，继承相同输入误解释风险；未来修复必须显式取得 WGS84 特征坐标，不能因修复 CRS 反而无意改变随机森林特征单位。本次未另行训练模型。

## Part V — Diagnostic Test Evidence

新增独立脚本 `tests/diagnose_crs.py`，名称不参与 pytest 自动发现。实际调用正式解析器、Session、七工具工厂中的计算工具及地图 Renderer；没有替换 GIS 算法、Session 或 Folium。直接 invoke 工具，不声称验证了真实 LLM 或完整 LangGraph 对话。模型工厂一旦被访问即失败，脚本清除本进程模型 Key 环境变量、阻止 socket 出站连接、关闭 PROJ 网络；不读取任何密钥值。

合成数据及参考：

- WGS84 `(9,51)` ↔ pyproj Transformer(always_xy=True) 生成的25832坐标；原 KD-CRS 还由已有固定常数测试保护。
- 25832 平面两点相差100m；1000m×1000m 方形面积1km²；严格内部一点计数1、密度1；参考数值来自构造的几何事实，不来自被测业务函数。
- 正确源 CRS 下的 Buffer 只验证不为空、位于输入附近及输出4326；地面500m半径另用 WGS84 Geod 独立判定，仍复现已知误差。
- 无 CRS、crs:null、无效 named-crs、子 Feature 声明、字段中 crs、显式错标4326、去掉集合CRS的 feature-list，均独立记录。
- D16 把同一 `(9,51)` 分别解释为不同 CRS，说明缺少来源时无法证明真实 CRS；没有将某个猜测标作答案。

共 **39 项诊断**：14 Reference Met（包括“不支持的原生 GDF 参数会被拒绝”等当前行为刻画），3 Known Defect 复现，21 Risk Reproduced，1 Inconclusive，0 Diagnostic Error。**这不是39个Passed，也不是21个新的独立缺陷**；很多条目是同一 CRS 数据流缺陷的不同输入或下游表现。脚本退出0仅说明诊断执行成功，不表示空间正确性全部通过。

| 诊断编号 | 本次证据 |
|---|---|
| D01、D21、D04-latlon | 合规 WGS84 GeoJSON、JSON lat/lon、CSV仍按现有约定解析 |
| D02、D03、D17 | 显式投影声明、未知/错误声明及明显不符合WGS84范围的坐标未被安全处理 |
| D05 | 转换器直接拒绝三种原生GeoDataFrame，原对象保持不变 |
| D06、D07 | 正确投影/混合CRS直接Core计算有独立数值参考；五种计算对无CRS输入均报错 |
| D08、D09、D10、D18、D19 | Session已有CRS仍被运输/重解析丢失；Dataset元数据可矛盾；feature-list丢声明 |
| D11、D12 | 四种字典地图路径未转换；Folium原生GDF GeoJSON路径确实转换 |
| D13八项、D20 | 对照真实Session→工具→派生提交；正确4326输入符合参考，原生25832路径错误；混合CRS误计0 |
| D14、D16 | 不确定性没有被端到端保留；真实CRS无法只靠坐标元组推断 |
| D15 | 独立地面半径验证，证明源CRS修复不等于Buffer误差修复 |

证据保留在本地忽略目录 `tests/logs/phase4c_crs_diagnostics.json`，含全部坐标、结果、警告、独立期望和分项状态。当前证据 SHA256：`66B8E1A9BEAE64CC88730E563492B11CEC78CB348D63499B80478456AE2CAE9A`。该日志可由脚本重复生成；重新运行后摘要或哈希可能随环境变化，不是冻结基准文件。

**Not Run**：真实浏览器 CRS 视觉/JavaScript 验收、真实 DeepSeek、在线 Nominatim、本次带模型的前端上传。上传解析及Dataset注册路径已直接诊断，前端接线为静态检查，不能替代完整页面操作。

## Part VI — Recommended Minimal Fix

以下全部是**待批准的方案**，本次没有实现。

### 1. 输入 CRS 判定集中在 gis/data_io.py

推荐保留来源坐标及其真实 CRS，展示时才建立 WGS84 副本，避免全量改变 Dataset 的数据形状或业务接口。

- 纯函数负责识别格式、确定 CRS 依据及冲突，和现有 extract_features_to_gdf 组合；不读 Session、不初始化LLM。
- 原生GeoDataFrame保留可信 .crs、复制数据；显式无CRS保持未知并拒绝分析。当前转换器不接受GDF，应在未来修复中明确补全此已存在于Session/Core之间的接口能力，而非声称它本来已支持。
- 继续把合法 CSV/JSON 的 lat/lon 作为经纬度协议；合规RFC7946 Feature/Collection使用格式约定。裸Feature列表应明确视为既有WGS84协议，不能推广成任意CRS默认。
- 旧式 named-crs 只在获批兼容策略下解析成 canonical CRS；未知、无法解析、linked-crs、显式null、互相冲突的声明不静默回退。
- 如增加可选 source_crs 参数，必须区分“参数未提供，允许检查格式协议”与“明确提供None，CRS未知”。不能再把两者合并为默认4326。
- 输入标签只能声明源 CRS，显示转换用 to_crs；不用 allow_override 自动修正矛盾，不根据坐标大小、文件名或LLM猜EPSG。

### 2. Dataset 保持可信且单一的源 CRS

- 复用现有 Dataset.crs 和 metadata 保存 canonical 源 CRS、依据（原生对象/旧声明/格式协议）、原声明；不要另建平行数据仓库，也不把“来源声明可信”描述成已证明真实地理位置。
- add_upload/派生提交的边界显式传入经过 Data I/O 判定的 CRS；现有原生GDF路径沿用其真实CRS，显式None不得变成4326。字典有显式crs却没有经判定的外部CRS时，应拒绝未准备好的注册调用，不能在Core内再次发明GIS默认策略。
- 手工 Dataset / 旧视图导入也要迁移到同一边界；GDF的data.crs与Dataset.crs矛盾应拒绝。CRS语义解析在GIS边界完成；Core只做契约一致性保护，不导入GIS、Agent或UI，不执行投影。
- 视图/resolve结果显式携带 Dataset.crs，包括None；feature-list不能依靠集合字段运输CRS。所有取数路径应统一，不能只修legacy_layers而遗漏ID直接resolve。
- 保留 Dataset/Layer ID、引用关系、锁、单工具提交及所有权规则；原始数据不因分析或展示被原地修改。

### 3. Agent 适配与 GIS Core

- 工具读取显式Session CRS，传入Data I/O转换器；缺失或冲突应在计算和提交前失败，不影响前一个成功工具的结果。
- buffer_geometries、pairwise_distances、calculate_area/count/density继续接收有可信CRS的GeoDataFrame；保持现有3857/25832、round、within和单位策略。
- 不能只修计算工具而遗漏可视化与随机森林。随机森林在提取lon/lat特征前显式取得WGS84副本，保持现有特征语义；不改模型训练算法、参数或工具Schema。
- CRS转换产生非有限坐标的直接错误应被识别；不要借机全面修复无效/空几何处理或面积精度，相关期望仍独立保护。

### 4. Renderer 取得显式准备好的经纬度数据

- 推荐由 gis/data_io.py 的轻量纯函数建立WGS84渲染副本，再由 frontend 的**显示与HTML导出两个调用点**准备显式图层配置。保留现有point-list/Feature/FeatureCollection的属性、字段、图层ID和样式约定；转换后CRS/bbox必须与坐标一致。
- maps.renderer继续仅消费经纬度渲染数据；可以在其输入契约处拒绝明确的非WGS84/未知状态，但不再负责猜源CRS，也不依赖Session。不要把坐标轴交换与投影转换混为一谈。
- 采用此接线可以保持当前 tests/test_app_integration.py:99–101 的依赖规则：frontend可以调用GIS和Maps；maps只依赖自身，不直接导入gis；core也不导入gis。简单在Renderer中import GIS解析器会违反已有架构测试，不推荐。
- 保持默认中心、最后图层bounds选择、图层顺序、色带和下载交互；真实坐标修正导致数值bounds变化是预期效果，不改变bounds策略。

因此完整最小修复预计涉及 gis/data_io.py、core/session.py 的边界传递、agent/tools.py、frontend.py 两处渲染准备及直接测试；maps/renderer.py最多加清晰的输入约束，core/contracts.py最多补充字段语义说明。无需修改Graph、LLM元数据策略、几何/统计公式、引入框架或新增依赖。这是设计范围，实际实施授权后仍须逐项核对。

## Part VII — Compatibility and Regression Risks

**Code-confirmed**：已有正确性测试的 projected_layer（tests/test_gis_correctness.py:25）先把解析构造的米制几何转换到4326再进入工具。因此原来全部回归通过，不能证明原生25832的完整输入链路正确。本次直接Core与Session路径对照填补了审查证据缺口，没有修改旧断言。

未来实施的验收建议：

1. 保留tests/test_gis_correctness.py:50原始(9,51)参考断言，修复后真实通过才移除对应strict xfail。
2. 将D02/D06/D08/D13/D20中的参考检查转成正式pytest：同一位置4326/25832、相同100m/1km²/计数1/密度1，覆盖原生GDF、显式GeoJSON和混合CRS。
3. 加未知None与缺省参数区分、非法声明、嵌套冲突、手工Dataset/旧导入、ID解析与feature-list运输测试；未知值必须报错或保留未知，不断言某个猜测EPSG。
4. 地图所有现有类型验证规范化后坐标与bounds；源GDF/字典/索引/属性/ID不变；显示和内存HTML导出使用同一准备逻辑。另做真实浏览器视觉验证。
5. 保留七工具名称、签名、顺序、Schema、状态和多步派生数据链路测试；确保随机森林特征单位不随源CRS改变。
6. 加“CRS错误不创建Dataset/Layer、不修改已有数据”测试；继续执行Session隔离、原子提交、导入和模块独立性回归。

兼容风险：原来静默接受的无CRS/矛盾声明将可能变为明确错误；公开Session注册调用若新增显式CRS要求，其直接调用者、测试fixture和README示例需同步迁移；不能只修UI。原始数据形状若统一变成GDF会扩大回归范围，推荐本次未来修复仍保持现有载荷形状。也不能只更改Dataset.crs字符串而不改变适配链路。

**Known Defect**：本次全部12项strict xfail原样保留。将来只修KD-CRS，理论上12→11；若同时给CSV加范围拒绝，KD-COORDINATES也可能转为通过，理论上再减1，必须作为额外批准的缺陷修复验收，不能出现未解释XPASS。KD-BUFFER、KD-TINY、KD-DENSITY、三个KD-GEOMETRY及其他工具/地图缺陷不因源CRS修复而自动解决。

## Part VIII — Decisions Requiring User Approval

| 决策 | 推荐方案 | 影响 |
|---|---|---|
| D-CRS-1：缺省WGS84的范围 | 仅CSV/JSON lat-lon既有协议、合规RFC7946及明确的既有WGS84 Feature列表；无CRS原生GDF或任意未知坐标报错 | 保持合法现有输入；停止无依据猜测；不新增用户CRS选择UI |
| D-CRS-2：旧GeoJSON支持范围 | 支持可离线解析的顶层named-crs（EPSG/OGC标识），尊重其源CRS；未知/null/linked/矛盾或子级覆盖先报清楚错误 | 满足现有KD-CRS参考，不把旧格式当RFC7946；是否支持唯一子级声明可另批准 |
| D-CRS-3：规范数据保留方式 | Dataset保留源坐标/CRS及现有载荷形状；仅渲染副本转4326 | 需要显式传CRS贯穿Session/工具；不改Session隔离机制或建立第二存储 |
| D-CRS-4：范围/有限值检查 | 批准CRS冲突及投影失败保护；CSV全量经纬度范围验证列为明确的附加项 | 后者会改变KD-COORDINATES状态；不应擅自连带修复 |

**Reasonable Inference / Decision Required**：即使标签可解析且数值在合理范围内，也不能证明输入真实来源正确。系统应要求调用者对声明负责；不能自动把“看起来像UTM”认成25832。固定分析投影是否将来扩展到全球自适应，留待独立方案，不属于本次源CRS最小修复的默认决策。

## Part IX — Actual Test Results and Git Status

实际命令：

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe tests/diagnose_crs.py
.venv\Scripts\python.exe -m pytest tests --collect-only -q
git diff --check
git status --short
git rev-parse HEAD
git rev-parse main
```

**Test-verified**：完整pytest退出0，**244 Passed、12 strict xfail，共256项，28.55s，0意外Failed、0XPASS**。12个缺陷项：KD-CRS、KD-BUFFER[51]、KD-TINY、KD-DENSITY、KD-GEOMETRY[empty/zero_area/self_intersection]、KD-LOOKUP、KD-MISSING-POINTS、KD-WEIGHT、KD-FEATURE、KD-COORDINATES。所有xfail装饰器均strict=True；一个参数化缺陷装饰器对应多个收集项。既有测试文件未修改。

新增诊断脚本后再次collect-only，仍为**256 tests collected（4.34s）**，确认诊断没有混入回归计数；新增脚本AST语法及两个新增文件尾随空白检查通过。

**Test-verified**：最终独立诊断运行退出0，39项；14 Reference Met、3 Known Defect、21 Risk Reproduced、1 Inconclusive、0脚本错误。它不增加pytest用例数，不把已复现的错误标成Passed。首次执行发现脚本文档字符串反斜杠的SyntaxWarning，已仅在该新增脚本中改为raw docstring并重新执行；最终运行无该警告。读取带emoji证据的一次终端打印遇到本机GBK输出编码错误，改为ASCII转义读取成功；与业务计算、JSON证据文件无关。

本阶段仅新增：

- tests/diagnose_crs.py：可重复离线诊断，含独立参考及真实模块调用。
- tests/PHASE4C_REPORT.md：本报告。

本地生成证据位于已有.gitignore覆盖的tests/logs/，不纳入Git。本次生产源码、既有测试、依赖及配置的tracked diff为空；暂存区为空；HEAD、分支、main未改变；backend.py仍不存在；git diff --check通过。工作区仅有上述两个预期未跟踪文件，因此结束时不是“完全干净”，但没有来源不明的修改。

**审查完成，修复尚未实施。** 已具备就以上四项策略进行决策和授权后实施最小修复的证据；当前CRS正确性尚未验收通过。本次在此停止。
