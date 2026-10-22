# GeoChat — Phase 3C: Agent 模块化实施报告

## Part I — Agent Extraction Summary

开始时核实：分支 refactor/modular-architecture，HEAD
`60328123de6c3c15f350e63327653d874b92622b`，工作区干净。
修改前完整测试 146 个：133 Passed、13 strict xfail。

按实施前给出的清单建立 agent/llm.py、agent/graph.py、agent/tools.py；backend.py 改为
重导出、延迟运行时及兼容包装。frontend.py、gis/、maps/ 无修改。
本批不实现 core/、Dataset/Layer 或完整 Session 隔离。

## Part II — New Files and Migrated Functions

| 原位置/功能 | 目标 | 迁移安排 |
|---|---|---|
| dotenv/environment/Streamlit secrets + ChatOpenAI 初始化 | agent/llm.py:create_llm | 按需创建；无导入期凭据检查或客户端构造 |
| smart_analyze_geodata | agent/llm.py:smart_analyze_geodata | 保留 JSON 提取、字段提示词和失败 fallback；支持显式模型 |
| AgentState | agent/graph.py:AgentState | 原字段和 add_messages reducer 保留 |
| agent_reasoner、System Prompt | agent/graph.py:agent_reasoner | 显式接收已绑定模型与 get_layers 回调 |
| should_continue | agent/graph.py:should_continue | 原条件路由直接迁移 |
| workflow/compile | agent/graph.py:build_workflow/create_agent_app | 工厂式构建，保留节点及边 |
| 重复 bind_tools、兼容运行入口 | agent/graph.py:AgentRuntime | 每个 runtime 创建/绑定/编译一次，按需执行 |
| 七个 @tool、TOOLS | agent/tools.py | 原名称、签名、顺序、返回文本和业务实现保留 |
| _SHARED_LAYER_BUS、set/get_agent_context | agent/tools.py | 唯一旧总线，backend 仅重导出同一对象/访问函数 |
| make_unique_layer_name、clean_gdf_for_geojson | agent/tools.py | 保留名称辅助及 inplace 清理兼容行为 |

agent/__init__.py 无初始化副作用。backend 继续重导出 GIS/地图入口和七工具；
旧 llm、llm_with_tools、workflow 属性使用模块 __getattr__ 转发到同一个延迟 runtime。
旧 agent_app.invoke(...) 和上传分析使用该 runtime，不重复创建模型。
并未承诺为旧 compiled graph 的全部扩展 API 提供代理；当前前端仅使用 invoke。

## Part III — Module Dependencies

```text
frontend → backend（兼容门面）
backend → agent.graph / agent.llm / agent.tools / gis / maps
agent.graph → LangGraph / LangChain messages（模型、工具、图层回调均显式传入）
agent.llm → LangChain messages；实际 create_llm 时才接入配置与 ChatOpenAI
agent.tools → LangChain tool / requests / GIS Core / NumPy / scikit-learn
```

agent/ 不导入 backend、frontend 或地图渲染器。graph 不实现 GIS 或 Folium。
LLM 只处理模型和辅助建议，确定性 CSV/GeoJSON 解析仍在 gis/data_io.py。
没有修改 GIS/Map 公共接口或增加依赖。

## Part IV — LLM Initialization and Import Independence

- create_llm 保留 deepseek-chat、https://api.deepseek.com、timeout=60、max_retries=2。
- 默认仍从仓库根 .env/environment 获取，再回退 Streamlit secrets；显式传入 key 时不读取配置。
- 纯 import agent.llm/graph/tools 或 backend 不创建客户端，不读配置；无 Key 不报错。
- 实际 create_llm 或 runtime 模型访问才校验 Key；缺失时报原 ValueError。
- 上传分析可接收显式模型；backend 包装复用默认 runtime 的模型。
- AgentRuntime 用 RLock 保护模型、绑定和 Graph 的单次初始化；这个锁不保护业务状态执行。
- 没有日志输出密钥。测试凭据均为合成字符串，dotenv/secrets 读取被隔离。

独立进程测试使用 Python -I 和最小系统环境，不继承 Key，禁止导入 backend、frontend、
Streamlit、maps/Folium、langchain_openai/OpenAI、dotenv，并阻止 socket 连接。
三个 agent 模块成功导入，使用显式 Mock 模型与真实 LangGraph 完成调用，无循环导入。
原 GIS 与地图独立进程测试也继续通过。

## Part V — LangGraph Workflow Compatibility

保留 START → agent → tools → agent / END 的单 Agent ReAct 流程、ToolNode 和消息 reducer。
模型绑定由原来的重复两次变为一次；工厂和兼容 runtime 均有独立测试。
原始模型与绑定模型在新增测试中为不同对象，避免假模型返回自身掩盖接线错误。

原系统提示词的文案、图层摘要和业务指引保留；迁移规范化了提示词空白行的尾随空格。
仍过滤输入中的 SystemMessage，并保留用户/AI/ToolMessage。每次 reasoner 重新读取图层回调。

覆盖：拓扑、分支路由、直接回答、单工具、多工具、失败返回模型、动态图层摘要及消息保留。
新增三步骤测试使用 Mock Nominatim 执行获取边界 → 100m Buffer → 修改结果样式。

## Part VI — Seven Tool Compatibility

七工具保留：fetch_city_boundary_osm、visualize_layer_on_map、calculate_buffer_analysis、
calculate_area_and_density、run_machine_learning_spatial_prediction、update_map_style、calculate_feature_distances。

AST 比较确认七个函数及四个辅助函数的实现保持，差异仅为部分 docstring 尾空格规范化；
TOOLS 顺序与内容不变，路由函数 AST 不变。既有工具 schema、默认值、错误路径、状态副作用测试通过。
新增 spy 测试在执行真实 GIS 函数的同时确认 Buffer/Area/Density 由适配器委托给 GIS Core。
Nominatim 仍使用 requests，可直接在 tools.requests 或兼容 backend.requests 上 Mock。

## Part VII — Existing Random Forest Preservation

随机森林函数整体迁入 agent/tools.py 的明确兼容区域，仍使用坐标特征、100 棵树和 random_state=42。
没有复制进 GIS Core，也没有删除或替换为 GNN。
既有真实 scikit-learn 小型合成数据回归继续验证确定性拟合、预测、残差、自动字段与错误输入。
仍是样本内拟合，没有新增外推评估或模型训练产品功能。

## Part VIII — Full Test Results

实际命令：

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
```

| 范围 | 总用例 | Passed | strict xfail | 意外 Failed / XPASS |
|---|---:|---:|---:|---:|
| 修改前基线 | 146 | 133 | 13 | 0 / 0 |
| 提取后、增加新测试前 | 146 | 133 | 13 | 0 / 0 |
| 新增 agent 模块测试 | 23 | 23 | 0 | 0 / 0 |
| 实施阶段全集 | 169 | 156 | 13 | 0 / 0 |
| Finalization 补充测试 | 8 | 8 | 0 | 0 / 0 |
| Finalization 最终全集 | 177 | 164 | 13 | 0 / 0 |

实施阶段实际运行 12.34s，退出码 0。日志本地保存 tests/logs/phase3c-run.log，不纳入版本控制。

Finalization 首先复跑原 169 个测试：156 Passed、13 strict xfail，11.80s；日志本地为
tests/logs/phase3c-review-baseline.log。随后补齐直接相关的八个测试：

- 一项验证 backend 元数据分析、直接 reasoner 和 agent_app.invoke 共用同一模型、绑定及运行时，
  并验证 should_continue 与未知兼容属性错误。
- 七项逐一验证真实 ToolNode 对全部七工具的分派、名称/调用 ID、原错误文本、反馈回模型及状态无误写。
  这些错误分派测试补充既有正常调用、Schema、GIS 和随机森林测试，并未替代它们。

最终按同一完整命令运行：**177 个用例，164 Passed、13 strict xfail、0 意外 Failed、0 XPASS**，
12.16s，退出码 0。最终日志本地为 tests/logs/phase3c-freeze-run.log，不提交。
Passed 从 156 增至 164 仅因新增八个测试；没有删除或放宽原断言。

既有两处测试基础设施按批准行为适配：

1. 原缺 Key 导入失败测试改为导入成功且 runtime 未初始化，再验证实际 create_llm 抛原错误。
2. 注册测试检查已绑定模型的 tools，而非假定原始模型在导入时已绑定。

其余既有断言及所有 strict xfail 未修改。实施阶段 23 个新增用例验证无凭据独立导入、延迟初始化、
模型参数、配置优先级、元数据成功/失败、单次与并发初始化、Graph/Prompt/Tools/兼容入口。
Mock 结果不代表真实 DeepSeek 理解、线上 Nominatim 或浏览器视觉验收通过；这些均未执行。

## Part IX — Known Defects and Remaining Risks

13 个 Known Defect 全部仍可复现，未删除、放宽或取消 strict xfail，未产生 XPASS。
包括全局会话串扰、CRS 覆盖、Buffer 地面误差、小面密度、无效几何、图层查找及地图输入等问题。

唯一旧总线位于 agent/tools.py。七工具全部通过 get_agent_layers 访问它；reasoner 通过显式回调读取。
backend._SHARED_LAYER_BUS 是同一对象的别名，不是第二份业务状态。单次初始化锁不修复串扰。
完整 Session 隔离、Dataset/Layer 身份及事务边界留待后续阶段。

工具字符串错误/静默降级、部分结果保留、前端工具轨迹缺失、历史投影及样本内随机森林语义仍保留。
backend 兼容门面仍需在后续集成收尾阶段移除。

## Part X — Git Diff and Modified Files

- 修改 backend.py：删除原业务实现，改为重导出/包装/单一延迟 runtime。
- 新增 agent/__init__.py、agent/llm.py、agent/graph.py、agent/tools.py。
- 修改 tests/test_agent_session.py、tests/test_tools.py：仅上述两项适配。
- 新增 tests/test_agent_modules.py 和本报告。
- frontend.py、gis/、maps/、依赖声明、原 GIS/地图及已知缺陷测试未改变。
- git diff --check 通过；候选源码无本机绝对路径或已发现的敏感内容。
- backend 原 Windows 只读属性在授权编辑期间临时解除，结束时恢复。
- Finalization 仅暂存上述九个文件，检查 staged diff 后建立独立本地提交
  `refactor: modularize agent graph tools and llm`；不 Push、Merge、Rebase、Reset 或修改 main。

### Finalization 审核结论

静态检查：七工具及四个辅助函数的实现/签名/注册顺序保持，System Prompt 除空白行尾空格外
内容一致；Agent 没有反向依赖 backend/frontend/maps；唯一旧总线定义仍在 agent/tools.py。
backend 仅有兼容包装和一个延迟 runtime，没有重复业务实现。GIS、Map、frontend、依赖及原 GIS/地图
和已知缺陷测试相对父提交无差异。候选文件未发现敏感信息、本机绝对路径或生成缓存。

实际执行：全部 177 个用例正常发现，13 个标记均 strict=True 且限定 AssertionError；
独立进程、单次/并发初始化、真实图循环、全部七工具分派、前端 AppTest、GIS/地图/RF 回归通过。
没有将 Mock/服务端测试解释为真实模型或浏览器验收。工作区未发现来源不明或范围外修改。

## Part XI — Readiness for Next Phase

具备提交 Phase 3C 审核的条件。LLM、Graph、Tools 分工明确，无新增测试阻塞；
旧前端调用、GIS、地图、随机森林及所有已知缺陷仍受回归覆盖。
下一阶段范围待批准；本次停止，不开展完整 Session/Contracts 重构或集成收尾。
