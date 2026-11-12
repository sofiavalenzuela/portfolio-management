"""Domain model for calculating a proposed stock portfolio rebalance."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    Overflow,
    Underflow,
    localcontext,
)
from enum import Enum
from fractions import Fraction

ZERO = Decimal("0")
ONE = Decimal("1")
CALCULATION_PRECISION = 28


class PortfolioValidationError(ValueError):
    """Raised when holdings or allocation targets cannot form a valid portfolio."""


class RebalanceValidationError(ValueError):
    """Raised when supplied prices cannot produce an actionable rebalance plan."""

    def __init__(self, issues: Iterable[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("Cannot calculate rebalance plan: " + "; ".join(self.issues))


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Stock:
    """A stock identity, represented by a normalized ticker symbol."""

    symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalize_stock_symbol(self.symbol))

    def current_price(self, last_available_price: Decimal) -> Decimal:
        """Validate and return the one supplied price used for this calculation."""
        if not isinstance(last_available_price, Decimal):
            raise ValueError("Price must be a Decimal.")
        if not last_available_price.is_finite() or last_available_price <= ZERO:
            raise ValueError("Price must be a finite value greater than zero.")
        return last_available_price


@dataclass(frozen=True)
class Holding:
    """The positive number of shares currently held for one stock."""

    stock: Stock
    quantity: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.stock, Stock):
            raise PortfolioValidationError("Holding stock must be a Stock.")
        _require_positive_decimal(self.quantity, "Holding quantity")


@dataclass(frozen=True)
class TargetAllocation:
    """The desired positive percentage of portfolio value for one stock."""

    stock: Stock
    target_weight: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.stock, Stock):
            raise PortfolioValidationError("Target allocation stock must be a Stock.")
        _require_positive_decimal(self.target_weight, "Target weight")
        if self.target_weight > ONE:
            raise PortfolioValidationError("Target weight must not exceed 1.")


@dataclass(frozen=True)
class Trade:
    """A proposed trade; it is not an order and does not change the portfolio."""

    stock: Stock
    side: TradeSide
    quantity: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.stock, Stock):
            raise PortfolioValidationError("Trade stock must be a Stock.")
        if not isinstance(self.side, TradeSide):
            raise PortfolioValidationError("Trade side must be a TradeSide.")
        _require_positive_decimal(self.quantity, "Trade quantity")


@dataclass(frozen=True, init=False)
class RebalancePlan:
    """The deterministic trade proposal required to reach the target allocation."""

    total_portfolio_value: Decimal
    trades: tuple[Trade, ...]

    def __init__(self, total_portfolio_value: Decimal, trades: Iterable[Trade]) -> None:
        _require_positive_decimal(total_portfolio_value, "Total portfolio value")
        immutable_trades = tuple(trades)
        if any(not isinstance(trade, Trade) for trade in immutable_trades):
            raise PortfolioValidationError("Rebalance plan trades must be Trade instances.")
        _require_unique_trade_symbols(immutable_trades)
        object.__setattr__(self, "total_portfolio_value", total_portfolio_value)
        object.__setattr__(self, "trades", immutable_trades)


@dataclass(frozen=True, init=False)
class Portfolio:
    """Immutable stock holdings and target allocations with rebalance calculation."""

    holdings: tuple[Holding, ...]
    target_allocations: tuple[TargetAllocation, ...]

    def __init__(
        self,
        holdings: Iterable[Holding],
        target_allocations: Iterable[TargetAllocation],
    ) -> None:
        object.__setattr__(self, "holdings", tuple(holdings))
        object.__setattr__(self, "target_allocations", tuple(target_allocations))
        self._validate_structure()

    def calculate_rebalance_plan(
        self, last_available_prices_by_symbol: Mapping[str, Decimal]
    ) -> RebalancePlan:
        """Return proposed trades without mutating this portfolio.

        The caller provides one price snapshot. Each stock validates its own supplied
        value, while this method verifies that all held and targeted stocks are priced.
        """
        stocks_by_symbol = self._stocks_by_symbol()
        prices, issues = self._validated_prices(stocks_by_symbol, last_available_prices_by_symbol)
        if issues:
            raise RebalanceValidationError(issues)

        holdings_by_symbol = {holding.stock.symbol: holding for holding in self.holdings}
        allocations_by_symbol = {
            allocation.stock.symbol: allocation for allocation in self.target_allocations
        }

        try:
            with localcontext(_new_calculation_context()):
                total_value = sum(
                    (holding.quantity * prices[holding.stock.symbol] for holding in self.holdings),
                    start=ZERO,
                )
                if total_value <= ZERO:
                    raise RebalanceValidationError(("Portfolio value must be greater than zero.",))

                exact_total_value = sum(
                    (
                        Fraction(holding.quantity) * Fraction(prices[holding.stock.symbol])
                        for holding in self.holdings
                    ),
                    start=Fraction(),
                )

                trades = tuple(
                    trade
                    for symbol, stock in sorted(stocks_by_symbol.items())
                    if (
                        trade := self._calculate_trade(
                            stock=stock,
                            price=prices[symbol],
                            total_value=exact_total_value,
                            holding=holdings_by_symbol.get(symbol),
                            allocation=allocations_by_symbol.get(symbol),
                        )
                    )
                    is not None
                )
        except DecimalException as error:
            raise RebalanceValidationError(
                (f"Decimal arithmetic failed: {type(error).__name__}.",)
            ) from error

        return RebalancePlan(total_portfolio_value=total_value, trades=trades)

    def _validate_structure(self) -> None:
        if any(not isinstance(holding, Holding) for holding in self.holdings):
            raise PortfolioValidationError("Portfolio holdings must be Holding instances.")
        if any(
            not isinstance(allocation, TargetAllocation) for allocation in self.target_allocations
        ):
            raise PortfolioValidationError(
                "Portfolio target allocations must be TargetAllocation instances."
            )
        if not self.target_allocations:
            raise PortfolioValidationError("Portfolio must have at least one target allocation.")
        _require_unique_symbols(self.holdings, "holdings")
        _require_unique_symbols(self.target_allocations, "target allocations")

        if not _target_weights_sum_to_one_exact(self.target_allocations):
            raise PortfolioValidationError("Target allocation weights must sum exactly to 1.")

    def _stocks_by_symbol(self) -> dict[str, Stock]:
        stocks = {holding.stock.symbol: holding.stock for holding in self.holdings}
        stocks.update(
            {allocation.stock.symbol: allocation.stock for allocation in self.target_allocations}
        )
        return stocks

    @staticmethod
    def _validated_prices(
        stocks_by_symbol: Mapping[str, Stock],
        last_available_prices_by_symbol: Mapping[str, Decimal],
    ) -> tuple[dict[str, Decimal], list[str]]:
        if not isinstance(last_available_prices_by_symbol, Mapping):
            return {}, ["Prices must be provided as a mapping."]

        prices: dict[str, Decimal] = {}
        conflicting_symbols: set[str] = set()
        for raw_symbol, price in last_available_prices_by_symbol.items():
            try:
                symbol = _normalize_stock_symbol(raw_symbol)
            except PortfolioValidationError:
                continue
            if symbol not in stocks_by_symbol:
                continue
            if symbol in prices:
                conflicting_symbols.add(symbol)
                continue
            prices[symbol] = price

        validated_prices: dict[str, Decimal] = {}
        issues = [
            f"Conflicting prices supplied for {symbol}." for symbol in sorted(conflicting_symbols)
        ]
        for symbol, stock in sorted(stocks_by_symbol.items()):
            if symbol not in prices:
                issues.append(f"Missing price for {symbol}.")
                continue
            try:
                validated_prices[symbol] = stock.current_price(prices[symbol])
            except ValueError as error:
                issues.append(f"Invalid price for {symbol}: {error}")
        return validated_prices, issues

    @staticmethod
    def _calculate_trade(
        *,
        stock: Stock,
        price: Decimal,
        total_value: Fraction,
        holding: Holding | None,
        allocation: TargetAllocation | None,
    ) -> Trade | None:
        current_quantity = holding.quantity if holding else ZERO
        current_value = Fraction(current_quantity) * Fraction(price)
        target_weight = allocation.target_weight if allocation else ZERO
        target_value = total_value * Fraction(target_weight)
        value_difference = target_value - current_value

        if value_difference == ZERO:
            return None

        side = TradeSide.BUY if value_difference > ZERO else TradeSide.SELL
        quantity = _fraction_to_decimal(abs(value_difference) / Fraction(price))
        return Trade(stock=stock, side=side, quantity=quantity)


def _require_positive_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal):
        raise PortfolioValidationError(f"{label} must be a Decimal.")
    if not value.is_finite() or value <= ZERO:
        raise PortfolioValidationError(f"{label} must be a finite value greater than zero.")


def _require_unique_symbols(
    entries: Iterable[Holding] | Iterable[TargetAllocation], label: str
) -> None:
    symbols = [entry.stock.symbol for entry in entries]
    if len(symbols) != len(set(symbols)):
        raise PortfolioValidationError(f"Portfolio contains duplicate {label}.")


def _normalize_stock_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise PortfolioValidationError("Stock symbol must be a string.")
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise PortfolioValidationError("Stock symbol must not be empty.")
    return normalized_symbol


def _new_calculation_context() -> Context:
    """Create a fresh, isolated arithmetic context for every plan calculation."""
    context = Context(prec=CALCULATION_PRECISION, rounding=ROUND_HALF_EVEN)
    context.traps[Overflow] = True
    context.traps[Underflow] = True
    return context


def _target_weights_sum_to_one_exact(allocations: Iterable[TargetAllocation]) -> bool:
    """Compare a finite collection of decimal weights exactly, without decimal rounding."""
    weights = tuple(allocation.target_weight for allocation in allocations)
    smallest_exponent = min(weight.as_tuple().exponent for weight in weights)
    if smallest_exponent > 0:
        return False

    total_units = sum(
        _decimal_coefficient(weight) * 10 ** (weight.as_tuple().exponent - smallest_exponent)
        for weight in weights
    )
    return total_units == 10 ** (-smallest_exponent)


def _decimal_coefficient(value: Decimal) -> int:
    coefficient = 0
    for digit in value.as_tuple().digits:
        coefficient = coefficient * 10 + digit
    return -coefficient if value.as_tuple().sign else coefficient


def _fraction_to_decimal(value: Fraction) -> Decimal:
    """Round a non-terminating fractional-share quantity in the calculation context."""
    return Decimal(value.numerator) / Decimal(value.denominator)


def _require_unique_trade_symbols(trades: Iterable[Trade]) -> None:
    symbols = [trade.stock.symbol for trade in trades]
    if len(symbols) != len(set(symbols)):
        raise PortfolioValidationError("Rebalance plan contains duplicate stock trades.")
