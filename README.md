# 分布式采集调度引擎

面向大规模数据采集团队的分布式采集调度引擎。覆盖采集节点集群管理、任务分片调度、结果聚合归并、节点健康监控、采集流量均衡、断点续采恢复与全局采集看板的完整分布式采集链路。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 采集节点集群 | `cluster` | 集群节点 | 节点编号、节点地址、节点角色 |
| 任务分片调度 | `shard` | 分片任务 | 分片编号、采集计划、分片范围 |
| 结果聚合归并 | `merge` | 聚合任务 | 任务编号、采集计划、分片总数 |
| 节点状态巡检 | `nodecheck` | 巡检记录 | 记录编号、节点编号、CPU使用率 |
| 采集流量均衡 | `balance` | 均衡策略 | 策略编号、负载算法、权重因子 |
| 断点续采恢复 | `resume` | 断点记录 | 断点编号、采集计划、断点位置 |
| 全局采集看板 | `dashboard` | 看板面板 | 面板编号、面板名称、显示指标 |
| 采集配置模板 | `crawlconf` | 配置模板 | 模板编号、模板名称、目标类型 |
| 采集数据源池 | `datasource` | 数据源条目 | 条目编号、数据源名称、源类型 |
| 采集日志解析 | `logparse` | 日志模板 | 模板编号、日志类型、解析规则 |
| 采集频率控制 | `ratelimit` | 频率规则 | 规则编号、目标站点、请求间隔 |
| 采集代理路由 | `proxy` | 代理配置 | 配置编号、代理地址、代理协议 |
| RPA执行记录 | `robot` | 执行记录 | 记录编号、流程模板、执行节点 |
| 采集结果校验 | `verify` | 校验规则 | 规则编号、采集计划、校验类型 |
| 采集操作审计 | `audit` | 审计记录 | 记录编号、操作类型、操作对象 |
| 采集存储分层 | `storage` | 存储策略 | 策略编号、存储层级、文件格式 |

## 节点状态巡检与节点模块的联动口径

节点状态巡检（`nodecheck`）与采集节点集群（`cluster`）共用一套身份与排序：

- **真实节点身份**：采样行上的 `节点ID` 指向集群节点主键；`节点编号` 只是展示编号。
  节点改号后，旧采样仍然跟着同一个真实节点，不会串到相邻节点。
- **排序与定位**：列表先按 `(采样时间, id)` 稳定排序再分页，`列表序号` 按过滤结果连续编号；
  `GET /api/nodecheck/locate?entry_id=…` 返回该记录在排序结果里的序号、页码与页内位置，
  详情、列表和翻页都用这一个身份核对。
- **按节点定位**：`node_id` 或 `节点编号` 参数都收敛成真实节点主键，只返回该节点自己的采样与处置；
  `GET /api/nodecheck/nodes/{id}`、`GET /api/cluster/nodes/{id}/inspection` 给出同一份视图。
- **处置幂等**：同一动作对同一条采样只生成一条处置记录（`_nodecheck_dispositions`），
  重复点击返回已有处置编号；并发提交在 `store.lock` 内判重。
- **采样幂等**：同一个 `记录编号`，或同一节点的 `(采样批次, 采样序号)` 重复到达都只落一条采样，
  不会产生第二份巡检异常清单。
- **可调度性**：巡检处置回写该真实节点的 `可调度` / `调度状态`（异常=已隔离，亚健康=告警可调度），
  集群节点上下线同样会重算，两处始终看同一个节点主键。
- **编号冲突两种口径**（`GET/POST /api/nodecheck/conflict-policy`）：
  - `origin` 沿用原始节点归属：详情保留采样携带的旧编号，新编号采样出现在待迁移清单；
  - `latest` 切换到最新采样引用：详情显示节点主数据新编号，旧记录归档并生成编号切换说明。
- **对外清单**：`/api/cluster/repair-records`（修复巡检记录）、`/api/cluster/pending-queue`（待处理队列）、
  `/api/cluster/exceptions`（巡检异常清单）、`/api/cluster/dispositions`（处置记录）供节点模块与
  另一个业务页面同步取用。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据采集、爬虫与RPA
