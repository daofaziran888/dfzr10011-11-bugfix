"""节点状态巡检接口：维护巡检记录，覆盖标记异常、隔离节点、恢复节点等动作。

接口口径：

* 列表、详情、定位、处置都按同一条记录身份（``id``）工作；
* 列表先排序再分页，``/locate`` 返回该记录在排序结果中的序号与页码；
* 按集群节点定位时只返回该真实节点自己的采样与处置，不串到相邻记录；
* 采样登记按节点 + 采样事件幂等，并发到达也只落一条巡检异常清单。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import AliasChoices

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.nodecheck import (
    CONFLICT_MODES,
    DEFAULT_CONFLICT_MODE,
    SORT_FIELDS,
    NodecheckService,
)

router = APIRouter(prefix="/api/nodecheck", tags=["节点状态巡检"])

service = NodecheckService()

LIST_FIELDS = ["记录编号", "节点编号", "CPU使用率", "内存使用率", "磁盘使用率", "网络延迟", "错误次数", "采样时间"]
STATUSES = ["正常", "亚健康", "异常", "已下线"]


def _check_sort(sort: str | None) -> None:
    if sort and sort not in SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"排序字段「{sort}」不受支持，可选：{'、'.join(SORT_FIELDS)}",
        )


def _check_size(size: int) -> None:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")


# ---------------------------------------------------------------------- 聚合视图
@router.get("/stats", response_model=dict)
def stats() -> dict[str, Any]:
    """巡检概览：总数、异常、待处理、归属冲突与按状态分布。"""
    return service.stats()


@router.get("/exceptions", response_model=dict)
def list_exceptions(
    node_id: int | None = Query(default=None, description="按真实节点主键过滤"),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code"), description="按集群节点编号过滤"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """巡检异常清单：一条采样一条清单，处置结果按记录身份回填。"""
    _check_size(size)
    items = service.list_exceptions()
    if node_id is not None:
        items = [item for item in items if item["节点ID"] == node_id]
    elif node_code:
        bundle = service.list_entries(node_code=node_code, page=1, size=10000)
        node_ids = {item["节点ID"] for item in bundle[0]}
        items = [item for item in items if item["节点ID"] in node_ids]
    total = len(items)
    start = max(page - 1, 0) * size
    page_items = items[start:start + size]
    for offset, item in enumerate(page_items):
        item["列表序号"] = start + offset + 1
    return {"items": page_items, "total": total, "page": page, "size": size}


@router.get("/pending", response_model=dict)
def list_pending(
    node_id: int | None = Query(default=None, description="按真实节点主键过滤"),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code"), description="按集群节点编号过滤"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """节点待处理队列：只包含仍待处置的采样，一处处置不会复制到相邻记录。"""
    _check_size(size)
    items = service.list_pending()
    if node_id is not None:
        items = [item for item in items if item["节点ID"] == node_id]
    elif node_code:
        bundle = service.list_entries(node_code=node_code, page=1, size=10000)
        node_ids = {item["节点ID"] for item in bundle[0]}
        items = [item for item in items if item["节点ID"] in node_ids]
    total = len(items)
    start = max(page - 1, 0) * size
    page_items = items[start:start + size]
    for offset, item in enumerate(page_items):
        item["列表序号"] = start + offset + 1
    return {"items": page_items, "total": total, "page": page, "size": size}


@router.get("/migration", response_model=dict)
def list_migration() -> dict[str, Any]:
    """待迁移清单：沿用原始节点归属时，新编号采样单列，便于复查。"""
    items = service.list_migration()
    return {
        "items": items,
        "total": len(items),
        "mode": service.conflict_mode(),
        "mode_name": CONFLICT_MODES[service.conflict_mode()],
    }


@router.get("/compare", response_model=dict)
def compare_nodes() -> dict[str, Any]:
    """巡检采样与采集节点集群的差异核对：按真实节点逐条比对。"""
    items = service.compare_nodes()
    return {
        "items": items,
        "total": len(items),
        "mismatched": sum(1 for item in items if not item["一致"]),
    }


@router.get("/conflict-policy", response_model=dict)
def get_conflict_policy() -> dict[str, Any]:
    """读取编号冲突处理口径：origin 沿用原始归属，latest 切换到最新采样引用。"""
    mode = service.conflict_mode()
    return {
        "mode": mode,
        "mode_name": CONFLICT_MODES[mode],
        "default": DEFAULT_CONFLICT_MODE,
        "options": [
            {"mode": key, "name": name, "description": _policy_description(key)}
            for key, name in CONFLICT_MODES.items()
        ],
    }


def _policy_description(mode: str) -> str:
    if mode == "latest":
        return "详情显示节点主数据的新编号，旧编号记录归档并生成编号切换说明"
    return "详情保留采样携带的旧编号，新编号采样进入待迁移清单"


@router.post("/conflict-policy", response_model=ActionResult)
def set_conflict_policy(payload: EntryPayload) -> ActionResult:
    """切换编号冲突处理口径，返回切换后的效果说明。"""
    mode = str(payload.merged_values().get("mode") or "").strip()
    if mode not in CONFLICT_MODES:
        return ActionResult(ok=False, message=f"未知的冲突处理口径「{mode}」，可选 origin / latest")
    service.set_conflict_mode(mode)
    migration = service.list_migration()
    return ActionResult(
        ok=True,
        message=(
            f"已切换到「{CONFLICT_MODES[mode]}」："
            + ("新编号采样进入待迁移清单" if mode == "origin" else "旧记录归档并生成编号切换说明")
        ),
        entry={"mode": mode, "mode_name": CONFLICT_MODES[mode], "migration_total": len(migration)},
    )


@router.get("/locate", response_model=dict)
def locate(
    entry_id: int = Query(description="巡检记录主键"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """在排序后的列表里定位记录，翻页后按同一序号与记录编号核对。"""
    _check_size(size)
    location = service.locate(entry_id, page=page, size=size)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"巡检记录 {entry_id} 不存在或已归档")
    return {**location, "记录": entry}


# ---------------------------------------------------------------------- 节点维度
@router.get("/nodes/{node_id}", response_model=dict)
def node_bundle(node_id: int) -> dict[str, Any]:
    """按集群节点读取巡检视图：采样、处置、异常清单、待处理队列。"""
    bundle = service.node_bundle(node_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"集群节点 {node_id} 不存在")
    return bundle


@router.get("/nodes/{node_id}/samples", response_model=dict)
def node_samples(
    node_id: int,
    page: int = 1,
    size: int = 20,
    include_archived: bool = False,
) -> dict[str, Any]:
    """只返回该真实节点自己的采样，按排序结果分页。"""
    _check_size(size)
    if service._node_by_id(node_id) is None:
        raise HTTPException(status_code=404, detail=f"集群节点 {node_id} 不存在")
    items, total = service.list_entries(
        node_id=node_id, page=page, size=size, include_archived=include_archived
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/nodes/{node_id}/dispositions", response_model=dict)
def node_dispositions(node_id: int) -> dict[str, Any]:
    """只返回该真实节点自己的处置记录。"""
    if service._node_by_id(node_id) is None:
        raise HTTPException(status_code=404, detail=f"集群节点 {node_id} 不存在")
    items = service.list_dispositions(node_id=node_id)
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------- 列表 / 导出
@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号或节点编号检索"),
    status: str | None = Query(default=None, description="正常、亚健康、异常、已下线"),
    node_id: int | None = Query(default=None, description="按真实节点主键定位"),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code"), description="按集群节点编号定位"),
    include_archived: bool = Query(default=False, description="是否包含已归档记录"),
    sort: str | None = Query(default=None, description="排序字段，默认按采样时间、id 稳定排序"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号、状态与真实节点过滤巡检列表；先排序后分页，空结果返回空页。"""
    _check_size(size)
    _check_sort(sort)
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        node_id=node_id,
        node_code=node_code,
        page=page,
        size=size,
        include_archived=include_archived,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    node_id: int | None = Query(default=None),
    node_code: str | None = Query(default=None, alias=AliasChoices("节点编号", "node_code")),
) -> dict[str, Any]:
    """导出巡检清单：与列表相同的排序与编号冲突口径。"""
    items = service.all_entries()
    if node_id is not None:
        items = [item for item in items if item["节点ID"] == node_id]
    elif node_code:
        items = [item for item in items if node_code in (item["节点编号"], item["采样节点编号"], item["记录编号"])]
    return {"module": "nodecheck", "total": len(items), "items": items}


# ---------------------------------------------------------------------- 登记采样
@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条巡检采样，缺字段或节点无法归属时说明原因而不是静默丢弃。"""
    entry, missing, message = service.create_entry(payload.merged_values())
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/samples", response_model=ActionResult)
def create_sample(payload: EntryPayload) -> ActionResult:
    """采样上报入口：同一节点同一次采样重复到达时幂等返回已有记录。"""
    return create_entry(payload)


# ---------------------------------------------------------------------- 单条记录
@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条巡检记录明细；明细与列表使用同一身份与排序定位。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"巡检记录 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行标记异常、隔离节点、恢复节点；同一动作重复提交只生成一条处置。"""
    action = str(payload.merged_values().get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    node = service._node_by_id(entry.get("节点ID"))
    return ActionResult(
        ok=True,
        message=message,
        entry=entry,
        node={
            "节点ID": entry.get("节点ID"),
            "节点编号": entry.get("节点编号"),
            "可调度": bool(node.get("可调度")) if node else False,
            "调度状态": str(node.get("调度状态") or "") if node else "",
        },
    )
