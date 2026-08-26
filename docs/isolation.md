# 策略隔离与审计完整性

> 适用版本：v0.1。契约层级见 [`docs/design/mvp-contract.md`](design/mvp-contract.md) §3.6。

策略与引擎的边界是 v0.1 的核心安全承诺。下面这些约束强制策略只通过 `Context` / `DataView` 与引擎交互，所有绕过都必须被拒。

## 1. `Order` 不可变

```python
@dataclass(frozen=True)
class Order:
    order_id: str
    symbol: str
    side: Side
    quantity: int          # BUY 已按整手向下取整
    avg_fill_price: Decimal | None = None
    fill_ids: tuple[str, ...] = ()   # tuple 而非 list
    status: OrderStatus = OrderStatus.ACCEPTED
```

- `@dataclass(frozen=True)`：策略拿到 `pending_orders()` 返回的 `Order` 后无法修改任何字段。
- `transition` / `record_fill` 使用 `object.__setattr__` 写入冻结字段——这层写入**只**由 engine / broker 调用。
- `fill_ids` 是 `tuple[str, ...]`，不是 `list`——防止策略 append 伪造 fill。
- 策略异常 / 构造期外赋 `fill_ids = ['fake']` 会被 `frozen=True` 抛 `FrozenInstanceError` 立即可见。

代码位置：`src/hqbacktest/domain/order.py`。

## 2. `DataView.portal` 私有

`DataView` 持有的原始 portal 在字段层面是 `_portal`（下划线），不是 `portal`：

```python
class DataView:
    def __init__(self, portal: MarketDataPortal, visible_through: str, ...):
        self._portal = portal          # 私有约定
        # self.portal 不存在；访问抛 AttributeError
```

- 策略无法经 `view.portal.get_bars(sym, future_date)` 读到 `visible_through` 之后的数据。
- 所有数据访问走 `view.history` / `view.current_price` / `view.universe`。
- 这一约定在契约层登记时**明确是 Python 的「约定私有」**，不是语言级强制力——策略仍可经 `view._portal`（或 `context._data_view._portal`）触及原 portal，这是 Python 语言的限制，不是项目缺陷。契约依靠**约定 + 审计测试**守住：

```python
# tests/engine/test_isolation.py
def test_strategy_cannot_access_raw_portal_by_public_name():
    view = DataView(portal=..., visible_through="20240101")
    with pytest.raises(AttributeError):
        view.portal
```

代码位置：`src/hqbacktest/data/view.py`、测试 `tests/engine/test_isolation.py`。

## 3. Universe 生效

```python
context.set_universe(["600000.SH", "000001.SZ"])
context.order("999999.SH", 100)   # → OUT_OF_UNIVERSE
```

- `set_universe([...])` 之后，对未声明的 symbol 下单**立即拒绝**。
- 拒绝原因：`RejectReason.OUT_OF_UNIVERSE`，事件流：
    - `ORDER_CREATED`（id 已分配）
    - `ORDER_REJECTED`（reason=`OUT_OF_UNIVERSE`）
- Order **不**经过 broker / portfolio，停留在 `engine._out_of_universe_orders`，在 `BacktestResult` 构造时折入 `orders_table`，便于审计。
- **未设 universe 时不限制**——v0.1 默认行为。

代码位置：`src/hqbacktest/engine/engine.py::_submit_intent`、`src/hqbacktest/engine/universe_filter.py`。

## 4. 历史股票池

`Context.historical_universe()`：

- 返回 `visible_through` 当日的 portal 股票池（默认排除 `.BJ`）。
- 受可见性约束：跨日访问需要先等引擎推进到合适的回测日。
- 不暴露原 `MarketDataPortal`。
- 与 `DataView.universe()` 行为一致（`Context.historical_universe` 转发该接口），契约 §3.3。

```python
def historical_universe(self) -> list[str]:
    return self._data_view.universe()   # 转发，不持有 portal
```

## 5. 返回值防御性

`pending_orders()` / `universe()` / `historical_universe()` **均返回 list 副本**（`list(self._internal)`），防止策略原地修改内部状态。

`Bar` / `Factor` 跨查询复用：同一引用在多个 `data.history()` 调用间共享——这是为减少 70 万 Bar 量级下的内存压力而做的、已经审计过的优化（见 [`docs/performance.md`](performance.md)）。策略不应就地修改 `Bar` 字段。

## 6. `Context` 只读

策略仅通过 `Context` 的查询方法读取现金、持仓、订单等；对 `Portfolio` 字段直接赋值（`context._portfolio.cash = ...`）必须抛错。这层保护依赖「私有字段名 + 审计测试」，与 §2 同源。

## 7. 审计测试清单

| 测试 | 文件 |
| --- | --- |
| `Order` 不可变（frozen + tuple fill_ids） | `tests/domain/test_order.py` |
| `DataView.portal` 是 `AttributeError` | `tests/engine/test_isolation.py::test_dataview_portal_is_private` |
| Universe 生效：未声明 symbol 拒绝 | `tests/engine/test_isolation.py::test_universe_filter_rejects_unknown_symbol` |
| `Context.historical_universe` 不暴露 portal | `tests/engine/test_isolation.py::test_historical_universe_does_not_expose_portal` |
| `pending_orders` / `universe` 返回 list 副本 | `tests/engine/test_isolation.py::test_return_values_are_defensive_copies` |
| 示例策略只触碰公共 API | `tests/examples/test_isolation_examples.py` |
