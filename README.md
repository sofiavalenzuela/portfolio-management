# Portfolio management

This project calculates a proposed stock rebalance plan. It does not execute trades or
modify holdings.

## Scope

- Stock holdings and stock allocation targets only.
- No cash, fees, tax, settlement, live price retrieval, or broker order execution.
- Prices are supplied to `calculate_rebalance_plan()` as one explicit snapshot.
- Fractional share quantities are supported with decimal arithmetic.

## Design

`Holding` represents current shares owned; `TargetAllocation` represents the desired
percentage of the invested portfolio. `Portfolio` combines them to produce a
`RebalancePlan` containing `BUY` and `SELL` trades.

The price map is the only market-data input. Keys are normalized in the same way as
stock symbols, so `" meta "` supplies the price for `META`. Multiple keys that normalize
to the same required symbol are rejected as conflicting. `Stock.current_price()`
validates one supplied price; it does not fetch, store, or cache a price.

`calculate_rebalance_plan()` does not mutate the portfolio and does not execute orders.
It values every held stock, calculates each stock's target value, and emits a trade for
each non-zero difference:

```text
current value = held quantity × price
target value  = total portfolio value × target weight
trade quantity = |target value - current value| ÷ price
```

Stocks held but absent from target allocations are sold. Stocks present in target
allocations but absent from holdings are bought.

## Validation and edge cases

Construction rejects empty symbols, duplicate holdings or allocations, and non-positive
or non-finite `Decimal` quantities and weights. At least one target is required; each
weight is at most `1`, and all weights must sum mathematically exactly to `1`.

Calculation requires a price mapping with one finite, positive `Decimal` price for every
held or targeted stock. Missing, invalid, conflicting, or non-mapping price input raises
`RebalanceValidationError`; errors for all affected required stocks are reported
together. Extra price entries for unrelated stocks are ignored.

An allocation-only portfolio is valid configuration, but cannot produce a plan in this
stock-only model because its invested value is zero; calculation raises
`RebalanceValidationError`. A successful `RebalancePlan` is immutable and contains at
most one proposed trade per stock.

## Precision

All calculations use `Decimal`, not binary floating-point. Target weights have no
artificial decimal-place limit. Rebalance calculations use a fixed local
28-significant-digit, half-even decimal context, so results do not depend on a caller's
mutable Decimal settings. Decimal overflow and underflow are reported as calculation
validation errors rather than leaking low-level arithmetic exceptions. Plans return the
resulting fractional-share quantities without applying a broker-specific trade
increment. Trade direction and existence are derived from the exact rational values of
the supplied decimals, preventing a very small non-zero rebalance difference from being
lost beside a large holding. Broker rounding, cash, fees, and execution belong to a
future layer.

## Development

Create an environment and install development dependencies:

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
```

The submission should include the LLM conversation transcript separately, as required
by the assignment.
