"""采集节点集群接口：维护集群节点，覆盖加入集群、下线节点、移除节点等动作。

采集节点集群同时承担节点模块的聚合入口：修复巡检记录、节点待处理队列、
巡检异常清单和处置结果都以真实节点身份在这里对外提供，供巡检页面和另一个
业务页面同步取用。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import AliasChoices

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.cluster import ClusterService

router = APIRouter(prefix="/api/cluster", tags=["采集节点集群"])

service = ClusterService()

LIST_FIELDS = ["节点编号", "节点地址", "节点角色", "CPU核心", "内存容量", "网络带宽", "心跳时间", "节点状态"]
STATUSES = ["待加入", "运行中", "已离线", "已移除"]
SORT_FIELDS = ("节点编号", "id")


def _check_size(size: int) -> None:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")


_NO_MATCH = object()


def _scope_node_id(node_id: int | None, node_code: str | None) -> Any:
    """把节点定位参数收敛成一个真实节点主键；编号不存在时返回未命中哨兵。"""
    if node_id is not None:
        return node_id
    if node_code:
        resolved = service.resolve_node_id(node_code)
        return resolved if resolved is not None else _NO_MATCH
    return None


def _page(items: list[dict[str, Any]], page: int, size: int) -> dict[str, Any]:
    start = max(page - 1, 0) * size
    page_items = items[start:start + size]
    for offset, item in enumerate(page_items):
        item["列表序号"] = start + offset + 1
    return {"items": page_items, "total": len(items), "page": page, "size": size}


# ---------------------------------------------------------------------- 巡检聚合
@router.get("/repair-records", response_model=dict)
def repair_records(
    node_id: int | None = Query(default=None, description="按真实节点主键定位"),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code"), description="按集群节点编号定位"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """节点模块的修复巡检记录：与巡检列表同一排序、同一编号冲突口径。"""
    _check_size(size)
    scoped = _scope_node_id(node_id, node_code)
    items = [] if scoped is _NO_MATCH else service.repair_records(node_id=scoped)
    return _page(items, page, size)


@router.get("/pending-queue", response_model=dict)
def pending_queue(
    node_id: int | None = Query(default=None),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code")),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """节点待处理队列：只放该节点自己仍待处置的采样。"""
    _check_size(size)
    scoped = _scope_node_id(node_id, node_code)
    items = [] if scoped is _NO_MATCH else service.pending_queue(node_id=scoped)
    return _page(items, page, size)


@router.get("/exceptions", response_model=dict)
def exception_list(
    node_id: int | None = Query(default=None),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code")),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """巡检异常清单：按真实节点过滤，处置按记录身份回填。"""
    _check_size(size)
    scoped = _scope_node_id(node_id, node_code)
    items = [] if scoped is _NO_MATCH else service.exception_list(node_id=scoped)
    return _page(items, page, size)


@router.get("/dispositions", response_model=dict)
def disposition_list(
    node_id: int | None = Query(default=None),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code")),
) -> dict[str, Any]:
    """处置记录：只返回该真实节点自己的处置，不复制到相邻记录。"""
    scoped = _scope_node_id(node_id, node_code)
    items = [] if scoped is _NO_MATCH else service.disposition_list(node_id=scoped)
    return {"items": items, "total": len(items)}


@router.get("/compare", response_model=dict)
def compare_nodes() -> dict[str, Any]:
    """节点状态巡检与采集节点集群的差异核对。"""
    items = service.inspection.compare_nodes()
    return {
        "items": items,
        "total": len(items),
        "mismatched": sum(1 for item in items if not item["一致"]),
    }


@router.post("/sync-schedulability", response_model=dict)
def sync_schedulability() -> dict[str, Any]:
    """按巡检异常处置重算全部真实节点的可调度性。"""
    items = service.sync_all()
    return {"items": items, "total": len(items)}


@router.get("/nodes/{node_id}/inspection", response_model=dict)
def node_inspection(node_id: int) -> dict[str, Any]:
    """单个真实节点的巡检视图：修复巡检记录、异常清单、待处理队列与可调度性。"""
    bundle = service.inspection_bundle(node_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"集群节点 {node_id} 不存在")
    return bundle


@router.post("/nodes/{node_id}/refresh-schedulability", response_model=ActionResult)
def refresh_schedulability(node_id: int) -> ActionResult:
    """接收巡检处置结果并更新对应真实节点的可调度性。"""
    item = service.refresh_schedulability(node_id)
    if item is None:
        return ActionResult(ok=False, message=f"集群节点 {node_id} 不存在")
    return ActionResult(
        ok=True,
        message=f"节点 {item['节点编号']} 可调度性已按巡检处置同步",
        entry=item,
        node={"节点ID": node_id, "节点编号": item["节点编号"],
              "可调度": item["可调度"], "调度状态": item["调度状态"]},
    )


# ---------------------------------------------------------------------- 列表 / 导出
@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按节点编号检索"),
    status: str | None = Query(default=None, description="待加入、运行中、已离线、已移除"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按节点编号与状态过滤采集节点集群列表；没有数据时返回空页，不报错。"""
    _check_size(size)
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集节点集群清单：带出巡检异常量与可调度性。"""
    nodes = service.sorted_nodes()
    items = [service._project(node) for node in nodes]
    for index, item in enumerate(items, start=1):
        item["列表序号"] = index
    return {"module": "cluster", "total": len(items), "items": items}


# ---------------------------------------------------------------------- 单条节点
@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条集群节点明细，附带该节点自己的修复巡检记录与待处理队列。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"集群节点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条集群节点，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.merged_values())
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if entry is None:
        return ActionResult(ok=False, message=f"节点编号 {payload.merged_values().get('节点编号')} 已存在")
    return ActionResult(ok=True, message="集群节点已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条集群节点执行加入集群、下线节点、移除节点；不允许的动作会被拦下。"""
    action = str(payload.merged_values().get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(
        ok=True,
        message=message,
        entry=entry,
        node={"节点ID": entry["节点ID"], "节点编号": entry["节点编号"],
              "可调度": entry["可调度"], "调度状态": entry["调度状态"]},
    )
