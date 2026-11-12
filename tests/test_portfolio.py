from dataclasses import FrozenInstanceError
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from portfolio_management import (
    Holding,
    Portfolio,
    PortfolioValidationError,
    RebalancePlan,
    RebalanceValidationError,
    Stock,
    TargetAllocation,
    Trade,
    TradeSide,
)

NON_FINITE_DECIMALS = (
    Decimal("NaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
)


def test_calculates_buy_and_sell_trades_to_reach_target_allocation() -> None:
    meta = Stock("META")
    aapl = Stock("AAPL")
    portfolio = Portfolio(
        holdings=(
            Holding(meta, Decimal("8")),
            Holding(aapl, Decimal("2")),
        ),
        target_allocations=(
            TargetAllocation(meta, Decimal("0.40")),
            TargetAllocation(aapl, Decimal("0.60")),
        ),
    )

    plan = portfolio.calculate_rebalance_plan({"META": Decimal("100"), "AAPL": Decimal("100")})

    assert plan.total_portfolio_value == Decimal("1000")
    assert plan.trades == (
        Trade(aapl, TradeSide.BUY, Decimal("4")),
        Trade(meta, TradeSide.SELL, Decimal("4")),
    )


def test_returns_no_trades_when_portfolio_is_already_at_target() -> None:
    meta = Stock("META")
    aapl = Stock("AAPL")
    portfolio = Portfolio(
        holdings=(
            Holding(meta, Decimal("4")),
            Holding(aapl, Decimal("6")),
        ),
        target_allocations=(
            TargetAllocation(meta, Decimal("0.40")),
            TargetAllocation(aapl, Decimal("0.60")),
        ),
    )

    plan = portfolio.calculate_rebalance_plan({"META": Decimal("100"), "AAPL": Decimal("100")})

    assert plan.trades == ()


def test_buys_target_only_stock_and_sells_holding_only_stock() -> None:
    old_stock = Stock("OLD")
    new_stock = Stock("NEW")
    portfolio = Portfolio(
        holdings=(Holding(old_stock, Decimal("4")),),
        target_allocations=(TargetAllocation(new_stock, Decimal("1")),),
    )

    plan = portfolio.calculate_rebalance_plan({"OLD": Decimal("100"), "NEW": Decimal("200")})

    assert plan.trades == (
        Trade(new_stock, TradeSide.BUY, Decimal("2")),
        Trade(old_stock, TradeSide.SELL, Decimal("4")),
    )


def test_preserves_high_precision_fractional_trade_quantities() -> None:
    meta = Stock("META")
    aapl = Stock("AAPL")
    portfolio = Portfolio(
        holdings=(Holding(meta, Decimal("1")),),
        target_allocations=(
            TargetAllocation(meta, Decimal("0.50")),
            TargetAllocation(aapl, Decimal("0.50")),
        ),
    )

    plan = portfolio.calculate_rebalance_plan({"META": Decimal("100"), "AAPL": Decimal("3")})

    assert plan.trades == (
        Trade(aapl, TradeSide.BUY, Decimal("50") / Decimal("3")),
        Trade(meta, TradeSide.SELL, Decimal("0.5")),
    )


def test_emits_trade_when_non_zero_difference_is_smaller_than_decimal_context_precision() -> None:
    meta = Stock("META")
    aapl = Stock("AAPL")
    portfolio = Portfolio(
        holdings=(
            Holding(meta, Decimal("1E+28")),
            Holding(aapl, Decimal("1")),
        ),
        target_allocations=(
            TargetAllocation(meta, Decimal("0.999999999999999999999999999999")),
            TargetAllocation(aapl, Decimal("1E-30")),
        ),
    )

    plan = portfolio.calculate_rebalance_plan({"META": Decimal("1"), "AAPL": Decimal("1")})

    assert plan.trades == (
        Trade(aapl, TradeSide.SELL, Decimal("0.9900000000000000000000000000")),
        Trade(meta, TradeSide.BUY, Decimal("0.9900000000000000000000000000")),
    )


def test_calculation_is_independent_of_callers_decimal_context() -> None:
    meta = Stock("META")
    aapl = Stock("AAPL")

    with localcontext() as caller_context:
        caller_context.prec = 4
        caller_context.rounding = ROUND_DOWN
        portfolio = Portfolio(
            holdings=(Holding(meta, Decimal("1")),),
            target_allocations=(
                TargetAllocation(meta, Decimal("0.50")),
                TargetAllocation(aapl, Decimal("0.50")),
            ),
        )
        plan = portfolio.calculate_rebalance_plan({"META": Decimal("100"), "AAPL": Decimal("3")})

    assert plan.trades[0] == Trade(
        aapl,
        TradeSide.BUY,
        Decimal("16.66666666666666666666666667"),
    )


def test_rejects_missing_prices_for_every_required_stock() -> None:
    portfolio = Portfolio(
        holdings=(Holding(Stock("META"), Decimal("1")),),
        target_allocations=(TargetAllocation(Stock("AAPL"), Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError) as error:
        portfolio.calculate_rebalance_plan({})

    assert set(error.value.issues) == {"Missing price for META.", "Missing price for AAPL."}


def test_normalizes_price_map_symbols() -> None:
    stock = Stock("META")
    portfolio = Portfolio(
        holdings=(Holding(stock, Decimal("1")),),
        target_allocations=(TargetAllocation(stock, Decimal("1")),),
    )

    plan = portfolio.calculate_rebalance_plan({" meta ": Decimal("100")})

    assert plan.trades == ()


def test_rejects_conflicting_normalized_price_symbols() -> None:
    stock = Stock("META")
    portfolio = Portfolio(
        holdings=(Holding(stock, Decimal("1")),),
        target_allocations=(TargetAllocation(stock, Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError) as error:
        portfolio.calculate_rebalance_plan({"META": Decimal("100"), " meta ": Decimal("101")})

    assert error.value.issues == ("Conflicting prices supplied for META.",)


def test_collects_missing_and_invalid_price_issues_in_symbol_order() -> None:
    portfolio = Portfolio(
        holdings=(Holding(Stock("META"), Decimal("1")),),
        target_allocations=(TargetAllocation(Stock("AAPL"), Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError) as error:
        portfolio.calculate_rebalance_plan({"META": Decimal("0")})

    assert error.value.issues == (
        "Missing price for AAPL.",
        "Invalid price for META: Price must be a finite value greater than zero.",
    )


def test_rejects_non_mapping_prices() -> None:
    stock = Stock("META")
    portfolio = Portfolio(
        holdings=(Holding(stock, Decimal("1")),),
        target_allocations=(TargetAllocation(stock, Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError, match="Prices must be provided as a mapping"):
        portfolio.calculate_rebalance_plan([])  # type: ignore[arg-type]


def test_rejects_invalid_prices() -> None:
    stock = Stock("META")
    portfolio = Portfolio(
        holdings=(Holding(stock, Decimal("1")),),
        target_allocations=(TargetAllocation(stock, Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError, match="Invalid price for META"):
        portfolio.calculate_rebalance_plan({"META": Decimal("0")})


@pytest.mark.parametrize("non_finite_value", NON_FINITE_DECIMALS)
def test_rejects_non_finite_holding_quantities_and_target_weights(
    non_finite_value: Decimal,
) -> None:
    stock = Stock("META")

    with pytest.raises(PortfolioValidationError):
        Holding(stock, non_finite_value)
    with pytest.raises(PortfolioValidationError):
        TargetAllocation(stock, non_finite_value)


@pytest.mark.parametrize("non_finite_value", NON_FINITE_DECIMALS)
def test_rejects_non_finite_prices_during_plan_calculation(non_finite_value: Decimal) -> None:
    stock = Stock("META")
    portfolio = Portfolio(
        holdings=(Holding(stock, Decimal("1")),),
        target_allocations=(TargetAllocation(stock, Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError, match="Invalid price for META"):
        portfolio.calculate_rebalance_plan({"META": non_finite_value})


@pytest.mark.parametrize("non_finite_value", NON_FINITE_DECIMALS)
def test_rejects_non_finite_values_in_public_value_objects(non_finite_value: Decimal) -> None:
    stock = Stock("META")

    with pytest.raises(ValueError):
        stock.current_price(non_finite_value)
    with pytest.raises(PortfolioValidationError):
        Trade(stock, TradeSide.BUY, non_finite_value)
    with pytest.raises(PortfolioValidationError):
        RebalancePlan(non_finite_value, ())


def test_rejects_zero_value_portfolio_when_only_targets_exist() -> None:
    portfolio = Portfolio(
        holdings=(),
        target_allocations=(TargetAllocation(Stock("META"), Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError, match="Portfolio value must be greater than zero"):
        portfolio.calculate_rebalance_plan({"META": Decimal("100")})


@pytest.mark.parametrize("invalid_value", (Decimal("0"), Decimal("-1")))
def test_rejects_non_positive_holding_quantities_and_target_weights(invalid_value: Decimal) -> None:
    stock = Stock("META")

    with pytest.raises(PortfolioValidationError):
        Holding(stock, invalid_value)
    with pytest.raises(PortfolioValidationError):
        TargetAllocation(stock, invalid_value)


def test_rejects_empty_and_duplicate_target_allocations() -> None:
    stock = Stock("META")

    with pytest.raises(PortfolioValidationError, match="at least one target allocation"):
        Portfolio(holdings=(Holding(stock, Decimal("1")),), target_allocations=())

    with pytest.raises(PortfolioValidationError, match="duplicate target allocations"):
        Portfolio(
            holdings=(Holding(stock, Decimal("1")),),
            target_allocations=(
                TargetAllocation(stock, Decimal("0.50")),
                TargetAllocation(stock, Decimal("0.50")),
            ),
        )


def test_rejects_invalid_holding_and_target_allocation_elements() -> None:
    stock = Stock("META")
    valid_holding = Holding(stock, Decimal("1"))
    valid_allocation = TargetAllocation(stock, Decimal("1"))

    with pytest.raises(PortfolioValidationError, match="holdings must be Holding instances"):
        Portfolio(holdings=(valid_holding, stock), target_allocations=(valid_allocation,))

    with pytest.raises(
        PortfolioValidationError,
        match="target allocations must be TargetAllocation instances",
    ):
        Portfolio(holdings=(valid_holding,), target_allocations=(valid_allocation, stock))


def test_rejects_duplicate_holdings_and_target_total_that_is_not_exactly_one() -> None:
    stock = Stock("META")

    with pytest.raises(PortfolioValidationError, match="duplicate holdings"):
        Portfolio(
            holdings=(Holding(stock, Decimal("1")), Holding(stock, Decimal("2"))),
            target_allocations=(TargetAllocation(stock, Decimal("1")),),
        )

    with pytest.raises(PortfolioValidationError, match="sum exactly to 1"):
        Portfolio(
            holdings=(Holding(stock, Decimal("1")),),
            target_allocations=(
                TargetAllocation(stock, Decimal("0.333333")),
                TargetAllocation(Stock("AAPL"), Decimal("0.333333")),
                TargetAllocation(Stock("MSFT"), Decimal("0.333333")),
            ),
        )


def test_rejects_target_total_that_only_rounds_to_one_at_calculation_precision() -> None:
    with pytest.raises(PortfolioValidationError, match="sum exactly to 1"):
        Portfolio(
            holdings=(Holding(Stock("META"), Decimal("1")),),
            target_allocations=(
                TargetAllocation(Stock("META"), Decimal("0.99999999999999999999999999996")),
                TargetAllocation(Stock("AAPL"), Decimal("0.00000000000000000000000000003")),
            ),
        )


def test_accepts_high_precision_target_total_that_is_mathematically_exact() -> None:
    portfolio = Portfolio(
        holdings=(Holding(Stock("META"), Decimal("1")),),
        target_allocations=(
            TargetAllocation(Stock("META"), Decimal("0.99999999999999999999999999996")),
            TargetAllocation(Stock("AAPL"), Decimal("0.00000000000000000000000000004")),
        ),
    )

    assert len(portfolio.target_allocations) == 2


@pytest.mark.parametrize(
    ("quantity", "price", "error_name"),
    (
        (Decimal("1E+999999"), Decimal("1E+999999"), "Overflow"),
        (Decimal("1E-999999"), Decimal("1E-999999"), "Underflow"),
    ),
)
def test_wraps_decimal_range_failures_as_rebalance_validation_errors(
    quantity: Decimal, price: Decimal, error_name: str
) -> None:
    stock = Stock("META")
    portfolio = Portfolio(
        holdings=(Holding(stock, quantity),),
        target_allocations=(TargetAllocation(stock, Decimal("1")),),
    )

    with pytest.raises(RebalanceValidationError, match=f"Decimal arithmetic failed: {error_name}"):
        portfolio.calculate_rebalance_plan({"META": price})


def test_normalizes_stock_symbols_without_mutating_portfolio_during_calculation() -> None:
    stock = Stock(" meta ")
    portfolio = Portfolio(
        holdings=(Holding(stock, Decimal("1")),),
        target_allocations=(TargetAllocation(stock, Decimal("1")),),
    )
    holdings_before_calculation = portfolio.holdings

    plan = portfolio.calculate_rebalance_plan({"META": Decimal("100")})

    assert stock.symbol == "META"
    assert plan.trades == ()
    assert portfolio.holdings == holdings_before_calculation


def test_rebalance_plan_is_immutable_and_defensively_copies_trades() -> None:
    trade = Trade(Stock("META"), TradeSide.BUY, Decimal("1"))
    source_trades = [trade]

    plan = RebalancePlan(Decimal("100"), source_trades)
    source_trades.append(Trade(Stock("AAPL"), TradeSide.SELL, Decimal("1")))

    assert plan.trades == (trade,)
    with pytest.raises(FrozenInstanceError):
        plan.trades = ()  # type: ignore[misc]


def test_rebalance_plan_rejects_duplicate_stock_trades() -> None:
    stock = Stock("META")

    with pytest.raises(PortfolioValidationError, match="duplicate stock trades"):
        RebalancePlan(
            Decimal("100"),
            (
                Trade(stock, TradeSide.BUY, Decimal("1")),
                Trade(stock, TradeSide.SELL, Decimal("1")),
            ),
        )
