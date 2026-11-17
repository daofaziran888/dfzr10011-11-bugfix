"""采集节点集群业务规则：节点主数据、可调度性与巡检联动都收在这里。

节点身份约定：``节点编号`` 是业务展示编号，``id`` 才是真实节点身份。巡检采样通过
``节点ID`` 绑定到这里，节点改号不会把采样带到别的节点；巡检异常处置回写
``可调度`` / ``调度状态``，只影响处置对应的那个真实节点。
"""
from __future__ import annotations

from typing import Any

from app.services.nodecheck import NodecheckService
from app.store import store

MODULE = "cluster"
REQUIRED_FIELDS = ["节点编号", "节点地址", "节点角色"]
STATUS_ORDER = ["待加入", "运行中", "已离线", "已移除"]
ACTION_RULES = {"加入集群": "运行中", "下线节点": "已离线", "移除节点": "已移除"}
NEGATIVE_ACTIONS = ["下线节点", "移除节点"]
UNSCHEDULABLE_STATUSES = ("待加入", "已离线", "已移除")


class ClusterService:
    def __init__(self) -> None:
        self.inspection = NodecheckService()

    # ------------------------------------------------------------------ 基础
    def resolve_node_id(self, node_code: str) -> int | None:
        """按编号解析真实节点主键，旧编号也能回溯到同一个节点。"""
        node = next(
            (row for row in self.sorted_nodes() if str(row.get("节点编号")) == str(node_code)),
            None,
        )
        if node is not None:
            return int(node["id"])
        resolved = self.inspection.resolve_node_code(str(node_code))
        return int(resolved["id"]) if resolved is not None else None

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self.sorted_nodes()
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("节点编号", "")) or keyword in str(row.get("节点地址", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [self._project(row) for row in rows[start:start + size]]
        for offset, row in enumerate(page_rows):
            row["列表序号"] = start + offset + 1
        return page_rows, total

    def sorted_nodes(self) -> list[dict[str, Any]]:
        """与巡检列表同一套稳定排序口径：按节点编号、id。"""
        return sorted(
            store.rows(MODULE),
            key=lambda row: (str(row.get("节点编号", "")), int(row.get("id", 0))),
        )

    def _project(self, row: dict[str, Any]) -> dict[str, Any]:
        node_id = int(row.get("id", 0))
        records = self.inspection.list_entries(node_id=node_id, page=1, size=10000)[0]
        return {
            **row,
            "节点ID": node_id,
            "可调度": bool(row.get("可调度")),
            "调度状态": str(row.get("调度状态") or "不可调度"),
            "巡检采样数": len(records),
            "待处置采样数": sum(1 for item in records if item.get("pending")),
            "巡检异常数": sum(1 for item in records if item.get("status") in ("亚健康", "异常")),
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        item = self._project(row)
        item["巡检异常清单"] = self.inspection.list_exceptions(node_id=int(row["id"]))
        item["待处理队列"] = self.inspection.list_pending(node_id=int(row["id"]))
        item["修复巡检记录"] = self.inspection.node_bundle(int(row["id"]))["巡检记录"]
        item["处置记录"] = self.inspection.list_dispositions(node_id=int(row["id"]))
        item["巡检核对"] = next(
            (
                item for item in self.inspection.compare_nodes()
                if item["节点ID"] == int(row["id"])
            ),
            None,
        )
        return item

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with store.lock:
            code = str(values.get("节点编号") or "").strip()
            for row in store.rows(MODULE):
                if str(row.get("节点编号", "")).strip() == code:
                    return None, []
            entry = {"id": store.next_id(MODULE)}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            entry["可调度"] = False
            entry["调度状态"] = "不可调度"
            entry["节点状态"] = STATUS_ORDER[0]
            store.rows(MODULE).append(entry)
            return self._project(entry), []

    # ------------------------------------------------------------------ 状态流转
    def _apply_schedulability(self, node: dict[str, Any]) -> None:
        status = str(node.get("status") or "")
        if status in UNSCHEDULABLE_STATUSES:
            node["可调度"] = False
            node["调度状态"] = "不可调度"
            return
        rebuilt = self.inspection.refresh_node_schedulability(int(node["id"]))
        if rebuilt is None:  # pragma: no cover - 防御性分支
            node["可调度"] = True
            node["调度状态"] = "可调度"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with store.lock:
            node = store.find(MODULE, entry_id)
            if node is None:
                return None, f"集群节点 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于采集节点集群可执行范围"
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            node["status"] = target
            node["节点状态"] = target
            node["pending"] = target != STATUS_ORDER[-1]
            node["abnormal"] = action in NEGATIVE_ACTIONS
            self._apply_schedulability(node)
            return self._project(node), f"集群节点已{action}，可调度性已按巡检结果同步"

    # ------------------------------------------------------------------ 巡检联动
    def refresh_schedulability(self, node_id: int) -> dict[str, Any] | None:
        """接收巡检处置结果，更新对应真实节点的可调度性。"""
        with store.lock:
            node = store.find(MODULE, int(node_id))
            if node is None:
                return None
            self._apply_schedulability(node)
            return self._project(node)

    def sync_all(self) -> list[dict[str, Any]]:
        with store.lock:
            for node in store.rows(MODULE):
                self._apply_schedulability(node)
            return [self._project(node) for node in self.sorted_nodes()]

    def inspection_bundle(self, node_id: int) -> dict[str, Any] | None:
        bundle = self.inspection.node_bundle(node_id)
        if bundle is None:
            return None
        node = store.find(MODULE, int(node_id))
        bundle["节点"] = self._project(node) if node is not None else None
        return bundle

    def repair_records(self, node_id: int | None = None) -> list[dict[str, Any]]:
        """修复巡检记录：与巡检模块共享同一批采样，按真实节点聚合。"""
        items, _ = self.inspection.list_entries(
            node_id=node_id, page=1, size=10000, include_archived=True
        )
        return items

    def pending_queue(self, node_id: int | None = None) -> list[dict[str, Any]]:
        return self.inspection.list_pending(node_id=node_id)

    def exception_list(self, node_id: int | None = None) -> list[dict[str, Any]]:
        return self.inspection.list_exceptions(node_id=node_id)

    def disposition_list(self, node_id: int | None = None) -> list[dict[str, Any]]:
        return self.inspection.list_dispositions(node_id=node_id)
