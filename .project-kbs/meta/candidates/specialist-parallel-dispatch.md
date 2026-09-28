---
type: candidate
title: 专家 agent 并发派发——parallel_safe 调度分组方案
slug: specialist-parallel-dispatch
status: open
created: 2026-09-24
updated: 2026-09-24
related_pages:
  - wiki/changes/0057_context-compression-v32.md
resolution: 待定
---

# 专家 agent 并发派发——parallel_safe 调度分组方案（候选）

## 背景与动机

2026-09-24 实测两起"模型表达并行意图但被调度层串行"的事件：

1. **delegate_task 三连单调用**（14:44）：模型计划"三个子代理并行调研 DGSA/WEDM/ETL"，落地却是三次连续单任务 `delegate_task` 调用，串行跑 7.5+13.4+10 分钟。当日已做工具面收敛：参数统一为单一 `tasks` 数组形态（1-3 项，多项线程池并行），description 点名"separate calls serialize"反模式。
2. **run_explore_agent 三连调用**（16:01）：模型换了 specialist 路径，连发三次独立 `run_explore_agent` tool_call（OpenAI 协议原生的并行表达），但 `_dispatch_tools` 的分组规则（read_only 连续段并行、非 read_only 独占串行）把它们串行化——**模型的并行意图被调度层浪费**。

关键认知：**并行安全不由模型说了算，由副作用说了算**。模型批次里也可能混顺序语义（先 write 后 verify），执行方必须保序写工具；但"模型想并行 + 副作用确实安全"的工具应该有通道。

## 方案：注册表新维度 + 分组规则扩展

### 落点（与 read_only 的既有模式完全同构）

1. `registry.register(..., parallel_safe: bool = False)` + `ToolEntry.parallel_safe` + 查询方法 `is_parallel_safe(name)`
2. 打标处：`specialist_agent_tool.py` 的 sync 循环按 def 计算；`delegate_task` 常量 True（其子代理恒 fresh，无 sticky）
3. 消费点（agent.py `_dispatch_tools` 分组循环）：

```python
_co = registry.is_read_only(name) or registry.is_parallel_safe(name)
# 连续 co 归并行组；串行组（写工具）独占——read-after-write 保序不变量不动
```

### parallel_safe 按专家 def 计算（核心设计点）

工具注册是一专家一工具，但 True/False 不打常量、按 def 属性算：

```python
def _def_is_parallel_safe(d) -> bool:
    if d.sticky_session: return False      # 并发互踩 {parent}:{slug} 会话
    if d.roster 含写面: return False        # 文件竞态 + 审批撞
    return True                             # 纯只读（explore/xihe_guide）
```

判断依据全部来自 def 已有字段（sticky_session + roster 只读性），零新配置项。选最保守档：只有纯只读专家并行。写面专家要并行时放宽只改这一个函数。

## 四个已核实的交互点

| 点 | 结论 |
|---|---|
| **sticky_session × 并发**（最重要） | 同 slug 两次 sticky 派发并发 → 共写 `{parent}:{slug}` 会话，语义污染（coder 默认 sticky！）。上述按 def 计算已规避；若未来放宽到非 sticky 写面，需 in-flight 检查串行化同 slug |
| **MidturnHub 单 pending 槽** | docstring 自述"mutually exclusive by construction (dispatch serializes batches)"——并行打破该构造假设。两子代理同撞审批门 → 第二个 busy 保守拒绝（不炸但误拒）。只读专家无审批工具不受影响；**已知边界文档化，暂不修** |
| **`_delegate_seq` 取号竞态** | 并发派发下 read-modify-write 撞号 → 桌面分组塌陷（同 label）。父级加小锁，~5 行 |
| 中断传播 / turn_base 预算 / 结果回填 | 现有机制已安全：`bind_current_agent` 按线程绑定；结果按 idx 顺序回填（tool_call_id 配对，完成序无关）；enforce_turn_budget 在 dispatch 后统一跑 |

## 已否决的替代路径

- **specialist 工具加 goals 数组**（schema 多任务形态）：同专家多任务与 delegate_task 批量场景重叠（explore×3 = delegate tasks×3 + 只读 toolsets）；sticky 语义在批量下天然别扭；跨专家并发仍无解。半天量级但不通用
- **YAML 显式 parallel 声明**：新配置项，违背零配置方向
- **模型侧教并行**（仅改 description）：对 delegate 有效（已做），对"连发多个 tool_call"的协议原生表达无效——模型已经在正确表达，是调度层没收

## 工作量与状态

主改动 ~50 行（维度+分组）+ 取号锁 ~5 行 + 测试；sticky 串行化若走按 def 计算则内含。总 **1-1.5 天**。风险集中在 sticky 边界与审批已知边界。

**暂不实施**——用户决策：先记录，后续按需评估。触发重新评估的信号：再次观察到多专家/多派发串行的明显等待浪费，或出现跨专家并发的真实任务形态。

## 相关

- [[0057_context-compression-v32]]：同日批次（工具面收敛 tasks 单形态的决策背景在此）
- 已落地的配套：specialist 派发标签编号（`explore-N`，共享 `_delegate_seq`）、桌面 trace 按 `by` 分组渲染、delegate bridge 的 tool_call_id 透传修复
