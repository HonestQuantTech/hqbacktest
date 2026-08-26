# 策略 API

> 适用版本：v0.1。契约层级见 [`docs/design/mvp-contract.md`](design/mvp-contract.md) §4「日交易日事件顺序」、规则 1「无未来函数」、规则 11「Context 只读不可改写」。

## 1. 生命周期回调

| 回调 | 能看到的数据 | 市价单最早成交时间 | 适合的工作 |
| --- | --- | --- | --- |
| `initialize(context)` | 无逐日行情（引擎强制读取即报错） | 不可下单（引擎强制） | 设置固定参数与初始 universe（`context.set_universe([...])`） |
| `before_trading_start(context, data)` | 前一交易日及以前 | 当日开盘 | 根据已知历史生成开盘订单 |
| `on_bar(context, data)` | 当日收盘后包含当天日线 | 下一交易日开盘 | 计算收盘信号并提交次日订单 |
| `after_trading_end(context)` | 当日完成后的账户快照 | 不可下单 | 记录、检查、自定义日志 |

- `on_bar` / `before_trading_start` 必须由策略**显式实现**或**显式继承默认空实现**——引擎不会自动注入任何默认值。
- `initialize` 中调用 `data.history()` / `data.current_price()` 必须抛错：盘前无任何逐日行情可读。
- `initialize` 中调用 `context.order(...)` 必须抛错：避免在未观察到任何数据时下单。
- 阶段顺序由 [`docs/design/mvp-contract.md`](design/mvp-contract.md) §4 锁定，**引擎不可重排或合并阶段**。

## 2. 时序图

```text
       D-1 收盘                 D 开盘      D 收盘
           │                       │           │
           └──── BAR_CLOSE(D-1) ───┘           │
                   │                            │
                   ▼                            │
    BEFORE_TRADING_START(D)   ◀── 策略看到 D-1 ──┘
                   │
                   ▼
         提交订单（当日开盘撮合候选）
                   │
                   ▼
            OPEN_MATCH(D) ─── 开盘价全额成交
                   │
                   ▼
             BAR_CLOSE(D) ─── 策略看到 D 收盘
                   │
                   ▼
        提交订单（最早 D+1 开盘撮合）
                   │
                   ▼
       AFTER_TRADING_END(D) ─── 日终估值与回调
```

## 3. `Context` API

### 3.1 只读查询

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `cash` | `Decimal` | 当前现金（不含当日冻结待扣） |
| `positions` | `dict[symbol, Position]` | 持仓快照（D+1 起始可卖数） |
| `total_equity` | `Decimal` | 现金 + 持仓市值 |
| `universe` | `list[str]` | `set_universe(...)` 声明的股票池；未设时返回空列表 |
| `historical_universe` | `list[str]` | `visible_through` 当日的 portal 股票池（默认排除 `.BJ`），受可见性约束；不暴露原 portal |
| `pending_orders()` | `list[Order]` | 所有未终止订单的副本（含 `PENDING` / `ACCEPTED`） |
| `current_price(symbol)` | `Decimal \| None` | 截至 `visible_through` 的最近有效收盘价；首日哨兵返回 `None`，无价返回 `None` |

### 3.2 下单意图

| 方法 | 签名 | 行为 |
| --- | --- | --- |
| `order(symbol, quantity)` | `quantity` 整数；正买负卖 | 按数量下单；BUY 整手向下、SELL 原值 |
| `order_value(symbol, value)` | `value` 接受 int / str 金额；float 拒绝 | 按金额下单，自动换算股数（整手约束） |
| `order_target(symbol, quantity)` | `quantity` 整数 | 调整到目标持仓；`0` 必须清仓 |
| `order_target_value(symbol, value)` | 同上 | 调整到目标金额 |
| `order_target_percent(symbol, pct)` | `pct` Decimal 占比 | 调整到目标百分比 |
| `cancel_order(order_id)` | 订单 ID | 撤销 `PENDING` / `ACCEPTED` 订单；已成交失败 |

代码位置：`src/hqbacktest/engine/context.py`。

### 3.3 不可改写

- 任何对 `context._portfolio` / `context._broker` 等私有字段的访问必须抛错。
- `Context` 是接口，`__setattr__` 对外暴露属性受校验。
- 见 [`docs/isolation.md`](isolation.md) §6。

## 4. `DataView` 可见性矩阵

`DataView` 在每次策略调用前由引擎设置 `visible_through`：

| 回调 | `visible_through` | `history()` 范围 | `current_price()` | 越界行为 |
| --- | --- | --- | --- | --- |
| `initialize` | 无（无逐日行情可读） | 调用即报错 | 调用即报错 | 抛 `DataViewError` |
| `before_trading_start(D)` | `D - 1` | `[..., D-1]` | 截至 D-1 最近有效 close | 越界抛错 |
| `on_bar(D)` | `D` | `[..., D]`，含 D 日线 | 截至 D 最近有效 close | 越界抛错 |
| `after_trading_end(D)` | `D` | `[..., D]` | 截至 D 最近有效 close | 越界抛错 |
| 首个交易日盘前 | `"00000000"` 哨兵 | `[]` | `None`（不抛异常） | — |

**任何未来数据访问必须抛错**，不得返回空值、最后已知值或插值结果。代码位置：`src/hqbacktest/data/view.py::history / current_price / universe`。

## 5. 示例：最小策略

```python
from decimal import Decimal
from hqbacktest import BaseStrategy


class MovingAverageStrategy(BaseStrategy):
    def initialize(self, context):
        context.set_universe(["600000.SH"])

    def before_trading_start(self, context, data):
        closes = data.history("600000.SH", field="close", bar_count=5)
        if len(closes) < 5:
            return
        avg = sum(closes) / len(closes)

    def on_bar(self, context, data):
        closes = data.history("600000.SH", field="close", bar_count=5)
        if len(closes) < 5:
            return
        avg = sum(closes) / len(closes)
        if closes[-1] > avg:
            context.order_target_percent("600000.SH", Decimal("0.95"))
        else:
            context.order_target("600000.SH", 0)

    def after_trading_end(self, context):
        pass
```

完整端到端：`examples/buy_and_hold.py`、`examples/moving_average.py`。

## 6. 生命周期测试

| 测试 | 文件 |
| --- | --- |
| 时序 + 数据可见性矩阵 | `tests/engine/test_lifecycle.py` |
| `initialize` 中调用 `data.history` 抛错 | `tests/engine/test_lifecycle.py::test_initialize_blocks_history` |
| `initialize` 中调用 `context.order` 抛错 | `tests/engine/test_lifecycle.py::test_initialize_blocks_order` |
| `on_bar` 在 `BAR_CLOSE` 后看到 D 日线 | `tests/engine/test_lifecycle.py::test_on_bar_sees_day_data` |
| `after_trading_end` 不可下单 | `tests/engine/test_lifecycle.py::test_after_trading_end_blocks_order` |
| 首日哨兵 `visible_through="00000000"` 不抛异常 | `tests/data/test_sentinel_first_day.py` |
