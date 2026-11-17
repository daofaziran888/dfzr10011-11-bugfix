"""节点状态巡检业务规则：采样归属、状态流转、字段校验与筛选口径都收在这里。

关键约束（与接口层共享的口径）：

* 一条采样记录只绑定一个真实节点，绑定身份是 ``节点ID``（集群节点主键），
  不是样例里带来的 ``节点编号`` 字符串；节点改号后旧采样仍然跟着同一个节点。
* 列表先按 ``(采样时间, id)`` 稳定排序再分页，详情按 ``id`` 取同一条记录，
  因此翻页、定位、处置都指向同一个身份。
* 同一巡检动作对同一条记录只会生成一条处置记录，重复提交按幂等返回。
* 采样登记按 ``(节点ID, 采样批次, 采样序号)`` 去重，并在仓库锁内完成，
  两个并发到达的同一次采样不会写出第二份巡检异常清单。
* 采样编号与节点主数据编号冲突时提供两种口径：
  ``origin`` 沿用原始节点归属（详情保留旧编号，待迁移清单给出新编号采样）；
  ``latest`` 切换到最新采样引用（详情显示新编号，旧记录归档并生成编号切换说明）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "nodecheck"
DISPOSITION_MODULE = "_nodecheck_dispositions"
REQUIRED_FIELDS = ["记录编号", "节点编号", "CPU使用率"]
STATUS_ORDER = ["正常", "亚健康", "异常", "已下线"]
ABNORMAL_STATUSES = ("亚健康", "异常")

# 动作 -> 处置后的巡检状态。恢复节点必须回到“正常”，否则节点永远停在已下线。
ACTION_RULES: dict[str, dict[str, Any]] = {
    "标记异常": {"status": "亚健康", "abnormal": True, "pending": True},
    "隔离节点": {"status": "异常", "abnormal": True, "pending": True},
    "恢复节点": {"status": "正常", "abnormal": False, "pending": False},
}
ACTION_ORDER = list(ACTION_RULES)

CONFLICT_POLICY_KEY = "nodecheck_conflict_policy"
CONFLICT_MODES = {
    "origin": "沿用原始节点归属",
    "latest": "切换到最新采样引用",
}
DEFAULT_CONFLICT_MODE = "origin"
SORT_FIELDS = ("采样时间", "id")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_sort_key(row: dict[str, Any]) -> tuple[str, int]:
    return (_text(row.get("采样时间")), int(row.get("id", 0)))


class NodecheckService:
    # ------------------------------------------------------------------ 基础定位
    @staticmethod
    def _node_by_id(node_id: Any) -> dict[str, Any] | None:
        value = _as_int(node_id)
        if value is None:
            return None
        return store.find("cluster", value)

    @staticmethod
    def _node_by_code(node_code: str) -> dict[str, Any] | None:
        code = _text(node_code)
        if not code:
            return None
        for node in store.rows("cluster"):
            if _text(node.get("节点编号")) == code:
                return node
        return None

    @classmethod
    def _resolve_node(cls, values: dict[str, Any]) -> dict[str, Any] | None:
        """把提交上来的节点身份落到集群节点主数据上，只认一个真实节点。"""
        node_id = _as_int(values.get("节点ID") or values.get("node_id"))
        node = cls._node_by_id(node_id) if node_id is not None else None
        if node is not None:
            return node
        return cls._node_by_code(_text(values.get("节点编号") or values.get("node_code")))

    @staticmethod
    def conflict_mode() -> str:
        mode = _text(store.meta.get(CONFLICT_POLICY_KEY)) or DEFAULT_CONFLICT_MODE
        return mode if mode in CONFLICT_MODES else DEFAULT_CONFLICT_MODE

    @staticmethod
    def set_conflict_mode(mode: str) -> str:
        if mode not in CONFLICT_MODES:
            raise ValueError(mode)
        with store.lock:
            store.meta[CONFLICT_POLICY_KEY] = mode
        return mode

    # ------------------------------------------------------------------ 投影
    def _project(self, row: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
        """把存储行投影成接口口径：同一身份、统一排序、按冲突策略决定编号与归档。"""
        mode = mode or self.conflict_mode()
        reported = _text(row.get("采样节点编号") or row.get("节点编号"))
        node = self._node_by_id(row.get("节点ID"))
        master_code = _text(node.get("节点编号")) if node is not None else ""
        conflict = bool(node is not None and master_code and reported != master_code)

        effective_code = reported
        archived = bool(row.get("归档"))
        archive_reason = _text(row.get("归档原因"))
        switch_note = _text(row.get("编号切换说明"))
        pending_migration = False

        if conflict and mode == "latest":
            archived = True
            archive_reason = "编号切换"
            effective_code = master_code
            switch_note = (
                f"巡检采样编号 {reported} 与节点主数据编号 {master_code} 不一致，"
                f"已切换到最新采样引用，旧记录归档"
            )
        elif conflict and mode == "origin":
            pending_migration = True
        elif archived and _text(row.get("节点编号")):
            effective_code = _text(row.get("节点编号"))

        item = dict(row)
        item.update({
            "id": int(row.get("id", 0)),
            "记录编号": _text(row.get("记录编号")),
            "节点ID": _as_int(row.get("节点ID")),
            "节点编号": effective_code,
            "采样节点编号": reported,
            "节点主数据编号": master_code,
            "归属模式": mode,
            "归属模式名称": CONFLICT_MODES[mode],
            "归属冲突": conflict,
            "待迁移": pending_migration,
            "归档": archived,
            "归档原因": archive_reason,
            "编号切换说明": switch_note,
            "真实节点可调度": bool(node.get("可调度")) if node is not None else False,
            "真实节点状态": _text(node.get("节点状态")) if node is not None else "",
            "真实节点调度状态": _text(node.get("调度状态")) if node is not None else "",
        })
        # 列表与详情都从同一条处置记录回填，保证重复提交不会在相邻记录上长出第二条处置。
        latest = self._latest_disposition(int(item["id"]))
        item["处置编号"] = latest.get("处置编号", "") if latest else ""
        item["处置动作"] = latest.get("动作", "") if latest else ""
        item["处置时间"] = latest.get("处置时间", "") if latest else ""
        item["处置结果"] = latest.get("处置结果", "") if latest else ""
        item["处置状态"] = "已处置" if latest else "待处置"
        return item

    def _is_archived(self, row: dict[str, Any]) -> bool:
        return bool(self._project(row).get("归档"))

    def _sorted_rows(self) -> list[dict[str, Any]]:
        return sorted(store.rows(MODULE), key=_row_sort_key)

    # ------------------------------------------------------------------ 查询
    def resolve_node_code(self, node_code: str) -> dict[str, Any] | None:
        """按编号找真实节点；节点改号后，旧编号也能通过采样回溯到同一个真实节点。"""
        node = self._node_by_code(node_code)
        if node is not None:
            return node
        with store.lock:
            for row in self._sorted_rows():
                if _text(node_code) in (
                    _text(row.get("采样节点编号")),
                    _text(row.get("节点编号")),
                ):
                    return self._node_by_id(row.get("节点ID"))
        return None

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        node_id: int | None = None,
        node_code: str | None = None,
        page: int = 1,
        size: int = 20,
        include_archived: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._sorted_rows()
        items = [self._project(row) for row in rows]
        if not include_archived:
            items = [item for item in items if not item["归档"]]

        # 节点定位只认真实节点主键；按编号定位先换成主键，绝不拿旧编号去做模糊匹配，
        # 否则相邻节点或改号前的采样会被一起带出来。旧编号能回溯到同一个真实节点。
        target_node_id: int | None = None
        if node_id is not None:
            target_node_id = _as_int(node_id)
        elif _text(node_code):
            # 旧编号也能回溯到同一个真实节点，定位结果仍然只包含该节点自己的采样。
            node = self.resolve_node_code(_text(node_code))
            target_node_id = _as_int(node.get("id")) if node is not None else -1
        if target_node_id is not None:
            items = [item for item in items if item["节点ID"] == target_node_id]

        text = _text(keyword)
        if text:
            matched: list[dict[str, Any]] = []
            for item in items:
                codes = {
                    item["记录编号"],
                    item["节点编号"],
                    item["采样节点编号"],
                    item["节点主数据编号"],
                }
                if text in item["记录编号"] or text in codes:
                    matched.append(item)
            items = matched
        if status:
            items = [item for item in items if item.get("status") == status]

        total = len(items)
        start = max(page - 1, 0) * size
        page_items = items[start:start + size]
        # 列表序号按当前过滤结果的稳定排序给出，翻页后仍然可以按序号和记录编号核对。
        for offset, item in enumerate(page_items):
            item["列表序号"] = start + offset + 1
        return page_items, total

    def all_entries(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """导出用：与列表完全相同的排序与投影口径。"""
        items = [self._project(row) for row in self._sorted_rows()]
        if not include_archived:
            items = [item for item in items if not item["归档"]]
        for index, item in enumerate(items):
            item["列表序号"] = index + 1
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        item = self._project(row)
        item["处置记录"] = self.list_dispositions(entry_id=entry_id)
        item["定位"] = self.locate(entry_id)
        return item

    def locate(self, entry_id: int, *, page: int = 1, size: int = 20) -> dict[str, Any]:
        """在默认排序的列表里定位记录，保证详情和列表指向同一条记录。"""
        items = [self._project(row) for row in self._sorted_rows()]
        for index, item in enumerate(items):
            if int(item.get("id", 0)) == int(entry_id):
                return {
                    "id": int(entry_id),
                    "记录编号": item["记录编号"],
                    "排序字段": list(SORT_FIELDS),
                    "列表序号": index + 1,
                    "页码": index // size + 1,
                    "页内位置": index % size + 1,
                    "size": size,
                    "page": page,
                }
        return {"id": int(entry_id), "记录编号": "", "列表序号": 0, "页码": 0, "页内位置": 0}

    def stats(self) -> dict[str, Any]:
        items = [self._project(row) for row in self._sorted_rows()]
        active = [item for item in items if not item["归档"]]
        return {
            "total": len(items),
            "active": len(active),
            "archived": len(items) - len(active),
            "pending": sum(1 for item in active if item["pending"]),
            "abnormal": sum(1 for item in active if item.get("abnormal")),
            "conflict": sum(1 for item in active if item["归属冲突"]),
            "migration": len([item for item in active if item["待迁移"]]),
            "by_status": {
                status: sum(1 for item in active if item.get("status") == status)
                for status in STATUS_ORDER
            },
        }

    # ------------------------------------------------------------------ 登记采样
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        # 节点身份两种写法都收：节点编号（可能还是旧编号）或真实节点主键 节点ID。
        has_node = bool(
            _text(values.get("节点编号"))
            or _text(values.get("节点ID"))
            or _text(values.get("node_code"))
            or _text(values.get("node_id"))
        )
        missing = [
            field for field in REQUIRED_FIELDS
            if field != "节点编号" and not _text(values.get(field))
        ]
        if not has_node:
            missing.append("节点编号")
        if missing:
            return None, missing, ""

        with store.lock:
            node = self._resolve_node(values)
            if node is None:
                return None, [], (
                    f"节点编号 {_text(values.get('节点编号')) or _text(values.get('节点ID')) or '（空）'} "
                    f"未匹配到集群节点主数据，采样无法归属"
                )
            node_id = int(node["id"])
            reported_code = _text(values.get("节点编号")) or _text(node.get("节点编号"))
            record_code = _text(values.get("记录编号"))
            batch = _text(values.get("采样批次"))
            seq = _as_int(values.get("采样序号"))

            # 幂等重放：同一个记录编号，或同一次采样事件重复到达，都返回已登记的记录。
            for row in store.rows(MODULE):
                same_record = record_code and _text(row.get("记录编号")) == record_code
                same_sample = (
                    batch
                    and seq is not None
                    and _as_int(row.get("节点ID")) == node_id
                    and _text(row.get("采样批次")) == batch
                    and _as_int(row.get("采样序号")) == seq
                )
                if same_record or same_sample:
                    return self._project(row), [], "该采样已登记，未重复生成巡检异常清单"

            entry_id = store.next_id(MODULE)
            status = _text(values.get("status")) or "正常"
            if status not in STATUS_ORDER:
                return None, [], f"巡检状态「{status}」不在允许的状态序列里"
            entry: dict[str, Any] = {"id": entry_id}
            for field in REQUIRED_FIELDS:
                entry[field] = _text(values.get(field))
            entry.update({
                "status": status,
                "pending": status in ABNORMAL_STATUSES,
                "abnormal": status in ABNORMAL_STATUSES,
                "节点ID": node_id,
                "节点编号": reported_code,
                "采样节点编号": reported_code,
                "采样批次": batch or f"BATCH-{entry_id:04d}",
                "采样序号": seq if seq is not None else 1,
                "采样时间": _text(values.get("采样时间")) or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "归档": False,
                "归档原因": "",
                "编号切换说明": "",
                "处置动作": "",
                "处置结果": "",
                "处置时间": "",
                "处置幂等键": "",
            })
            for field in ("内存使用率", "磁盘使用率", "网络延迟", "错误次数"):
                if _text(values.get(field)):
                    entry[field] = _text(values.get(field))
            store.rows(MODULE).append(entry)
            # 采样本身不改节点可调度性；只有巡检处置才会推动节点集群更新。
            return self._project(entry), [], "巡检记录已登记"

    # ------------------------------------------------------------------ 处置
    def _find_disposition(self, entry_id: int, action: str) -> dict[str, Any] | None:
        for record in store.rows(DISPOSITION_MODULE):
            if int(record.get("记录ID", 0)) == int(entry_id) and record.get("动作") == action:
                return record
        return None

    def _record_disposition(
        self,
        entry: dict[str, Any],
        action: str,
        *,
        node: dict[str, Any] | None,
    ) -> dict[str, Any]:
        rows = store.rows(DISPOSITION_MODULE)
        disposition_id = store.next_id(DISPOSITION_MODULE)
        record = {
            "id": disposition_id,
            "处置编号": f"DISP-{disposition_id:04d}",
            "记录ID": int(entry["id"]),
            "记录编号": _text(entry.get("记录编号")),
            "节点ID": _as_int(entry.get("节点ID")),
            "节点编号": _text(node.get("节点编号")) if node is not None else _text(entry.get("节点编号")),
            "采样节点编号": _text(entry.get("采样节点编号")) or _text(entry.get("节点编号")),
            "动作": action,
            "处置结果": _text(entry.get("status")),
            "可调度": bool(node.get("可调度")) if node is not None else False,
            "调度状态": _text(node.get("调度状态")) if node is not None else "",
            "幂等键": f"{_text(entry.get('记录编号')) or entry['id']}:{action}",
            "处置时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows.append(record)
        return dict(record)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        action = _text(action)
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"巡检记录 {entry_id} 不存在或已归档"
            projected = self._project(entry)
            if projected["归档"]:
                return None, f"巡检记录 {projected['记录编号']} 已归档，仅可查看"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于节点状态巡检可执行范围"

            node = self._node_by_id(entry.get("节点ID"))
            if node is None:
                return None, f"巡检记录 {entry_id} 未绑定真实节点，无法处置"
            if _text(node.get("节点状态")) in ("已移除",):
                return None, f"节点 {_text(node.get('节点编号'))} 已移除，无法执行巡检处置"

            existing = self._find_disposition(int(entry["id"]), action)
            if existing is not None:
                # 同一动作重复点击：不写第二条处置，也不把已推进的状态回拨。
                return self._project(entry), (
                    f"巡检记录 {projected['记录编号']} 的「{action}」已处置"
                    f"（处置编号 {existing['处置编号']}），本次未重复生成"
                )

            rule = ACTION_RULES[action]
            entry["status"] = rule["status"]
            entry["pending"] = rule["pending"]
            entry["abnormal"] = rule["abnormal"]
            entry["处置动作"] = action
            entry["处置结果"] = rule["status"]
            entry["处置时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry["处置幂等键"] = f"{_text(entry.get('记录编号')) or entry['id']}:{action}"

            self.refresh_node_schedulability(int(node["id"]))
            node = self._node_by_id(entry.get("节点ID")) or node
            self._record_disposition(entry, action, node=node)
            return self._project(entry), f"巡检记录已{action}，节点 {_text(node.get('节点编号'))} 可调度性已同步"

    def list_dispositions(
        self,
        *,
        entry_id: int | None = None,
        node_id: int | None = None,
    ) -> list[dict[str, Any]]:
        records = sorted(store.rows(DISPOSITION_MODULE), key=lambda row: int(row.get("id", 0)))
        if entry_id is not None:
            records = [row for row in records if int(row.get("记录ID", 0)) == int(entry_id)]
        if node_id is not None:
            records = [row for row in records if _as_int(row.get("节点ID")) == _as_int(node_id)]
        return [dict(row) for row in records]

    # ------------------------------------------------------------------ 节点可调度性
    def _active_node_rows(self, node_id: int) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(MODULE)
            if _as_int(row.get("节点ID")) == int(node_id) and not self._is_archived(row)
        ]

    def refresh_node_schedulability(self, node_id: int) -> dict[str, Any] | None:
        """按该真实节点自己名下的采样汇总可调度性，不参考相邻记录。"""
        node = store.find("cluster", int(node_id))
        if node is None:
            return None
        rows = self._active_node_rows(int(node_id))
        node_status = _text(node.get("节点状态"))
        if node_status in ("已离线", "已移除"):
            node["可调度"] = False
            node["调度状态"] = "不可调度"
        elif any(_text(row.get("status")) == "异常" for row in rows):
            node["可调度"] = False
            node["调度状态"] = "已隔离"
        elif any(_text(row.get("status")) == "亚健康" for row in rows):
            node["可调度"] = True
            node["调度状态"] = "告警可调度"
        else:
            node["可调度"] = True
            node["调度状态"] = "可调度"
        node["abnormal"] = any(bool(row.get("abnormal")) for row in rows)
        node["pending"] = any(bool(row.get("pending")) for row in rows)
        return node

    # ------------------------------------------------------------------ 异常 / 待处理 / 迁移
    def _latest_disposition(self, entry_id: int) -> dict[str, Any] | None:
        records = self.list_dispositions(entry_id=entry_id)
        return records[-1] if records else None

    def list_exceptions(self, *, node_id: int | None = None) -> list[dict[str, Any]]:
        """巡检异常清单：一条采样一条清单，按记录身份聚合处置结果。"""
        items = [
            self._project(row) for row in self._sorted_rows()
            if not self._is_archived(row) and _text(row.get("status")) in ABNORMAL_STATUSES
        ]
        if node_id is not None:
            items = [item for item in items if item["节点ID"] == _as_int(node_id)]
        return items

    def list_pending(self, *, node_id: int | None = None) -> list[dict[str, Any]]:
        """节点待处理队列：仍标记 pending 的采样，一处处置不会复制到相邻记录。"""
        items = [
            self._project(row) for row in self._sorted_rows()
            if not self._is_archived(row) and bool(row.get("pending"))
        ]
        if node_id is not None:
            items = [item for item in items if item["节点ID"] == _as_int(node_id)]
        return items

    def list_migration(self, *, mode: str | None = None) -> list[dict[str, Any]]:
        """待迁移清单：沿用原始节点归属时，把新编号下的采样单列出来。"""
        mode = mode or self.conflict_mode()
        if mode != "origin":
            return []
        items = []
        for row in self._sorted_rows():
            item = self._project(row, mode)
            if not item["归属冲突"]:
                continue
            items.append({
                **item,
                "迁移前编号": item["采样节点编号"],
                "迁移后编号": item["节点主数据编号"],
                "迁移说明": (
                    f"采样携带编号 {item['采样节点编号']}，节点主数据最新编号为"
                    f" {item['节点主数据编号']}，待迁移到该编号"
                ),
            })
        return items

    # ------------------------------------------------------------------ 巡检 vs 集群
    def compare_nodes(self) -> list[dict[str, Any]]:
        """检查巡检采样到采集节点集群的差异，按真实节点逐一核对。"""
        comparisons: list[dict[str, Any]] = []
        for node in sorted(store.rows("cluster"), key=lambda row: int(row.get("id", 0))):
            node_id = int(node["id"])
            rows = [row for row in self._active_node_rows(node_id)]
            rows.sort(key=_row_sort_key)
            latest = rows[-1] if rows else None
            inspection_status = _text(latest.get("status")) if latest else "无采样"
            node_status = _text(node.get("节点状态"))
            expected_schedulable = (
                node_status == "运行中" and inspection_status not in ("异常", "已下线", "无采样")
            )
            actual_schedulable = bool(node.get("可调度"))
            differences: list[str] = []
            if latest is None:
                differences.append("集群节点没有巡检采样")
            elif inspection_status == "已下线" and node_status != "已离线":
                differences.append("巡检判定已下线，但集群节点仍在线")
            elif node_status == "已离线" and inspection_status not in ("已下线", "无采样"):
                differences.append("集群节点已离线，但最近一次巡检未记录已下线")
            elif inspection_status == "异常" and actual_schedulable:
                differences.append("巡检已隔离，但集群节点仍可调度")
            elif inspection_status in ("正常", "亚健康") and not actual_schedulable and node_status == "运行中":
                differences.append("巡检可调度，但集群节点被标记不可调度")
            comparisons.append({
                "节点ID": node_id,
                "节点编号": _text(node.get("节点编号")),
                "集群节点状态": node_status,
                "集群调度状态": _text(node.get("调度状态")),
                "集群可调度": actual_schedulable,
                "巡检状态": inspection_status,
                "最近采样记录": _text(latest.get("记录编号")) if latest else "",
                "最近采样时间": _text(latest.get("采样时间")) if latest else "",
                "期望可调度": expected_schedulable,
                "一致": not differences,
                "差异说明": differences,
                "采样数": len(rows),
                "未归档采样数": len(rows),
            })
        return comparisons

    # ------------------------------------------------------------------ 节点维度视图
    def node_bundle(self, node_id: int) -> dict[str, Any] | None:
        """供集群节点模块复用的巡检视图：采样、处置、异常清单、待处理队列。"""
        node = self._node_by_id(node_id)
        if node is None:
            return None
        node_id = int(node["id"])
        records = [
            self._project(row) for row in self._sorted_rows() if _as_int(row.get("节点ID")) == node_id
        ]
        for index, item in enumerate(records, start=1):
            item["列表序号"] = index
        return {
            "节点ID": node_id,
            "节点编号": _text(node.get("节点编号")),
            "可调度": bool(node.get("可调度")),
            "调度状态": _text(node.get("调度状态")),
            "巡检记录": records,
            "处置记录": self.list_dispositions(node_id=node_id),
            "巡检异常清单": self.list_exceptions(node_id=node_id),
            "待处理队列": self.list_pending(node_id=node_id),
        }
