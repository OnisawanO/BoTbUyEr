"""Seller and advice agents for the Track 3 practice market."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf


@dataclass
class Offer:
    seller: str
    batch_id: str
    quality: str
    reference_price: float
    ask_price: float
    markup: float


class AdviceAgent:
    """A configurable, non-LLM pricing adviser with independent learning state."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.kind = settings["advice_type"]
        self.target_markup = float(settings.get("initial_markup", 0.12))
        self.price_outcomes: dict[tuple[str, int], list[int]] = {}

    def recommend(
        self,
        batch: dict,
        competitor_offers: list[Offer],
        inventory_units: int,
        initial_inventory: int,
        rounds_left: int,
        total_rounds: int,
    ) -> float:
        low = float(self.settings.get("min_markup", -0.10))
        high = float(self.settings.get("max_markup", 0.30))
        base = float(self.settings.get("initial_markup", 0.12))
        kind = self.kind

        if kind == "competitor_undercut":
            comparable = [o for o in competitor_offers if o.quality == batch["quality"]]
            if not comparable:
                comparable = competitor_offers
            if comparable:
                cheapest = min(comparable, key=lambda offer: offer.ask_price)
                target_price = cheapest.ask_price - float(self.settings.get("undercut_amount", 1.0))
                return self._clip(target_price / batch["reference_price"] - 1, low, high)
            return self._clip(base, low, high)

        if kind == "inventory_clearance":
            stock_ratio = inventory_units / max(initial_inventory, 1)
            time_pressure = 1 - (rounds_left / max(total_rounds, 1))
            adjustment = (
                float(self.settings.get("stock_pressure", 0.10)) * stock_ratio
                + float(self.settings.get("time_pressure", 0.20)) * time_pressure
            )
            return self._clip(base - adjustment, low, high)

        if kind == "margin_feedback":
            return self._clip(self.target_markup, low, high)

        if kind == "expected_payoff":
            candidates = [float(x) for x in self.settings["candidate_markups"]]
            prior_rate = float(self.settings.get("prior_win_rate", 0.65))
            price_sensitivity = float(self.settings.get("prior_price_sensitivity", 1.0))
            prior_strength = float(self.settings.get("prior_strength", 4.0))
            continuation = -0.20 + 0.20 * (
                max(rounds_left - 1, 0) / max(total_rounds - 1, 1)
            )
            best_markup, best_value = candidates[0], -inf
            for candidate in candidates:
                bucket = self._price_bucket(candidate)
                wins, losses = self.price_outcomes.get((batch["quality"], bucket), [0, 0])
                prior = min(0.95, max(0.05, prior_rate - price_sensitivity * max(candidate, 0)))
                predicted_sale = (wins + prior_strength * prior) / (
                    wins + losses + prior_strength
                )
                expected = predicted_sale * candidate + (1 - predicted_sale) * continuation
                # In a tie, favor the lower ask to reflect buyer price sensitivity.
                if expected > best_value or (expected == best_value and candidate < best_markup):
                    best_markup, best_value = candidate, expected
            return self._clip(best_markup, low, high)

        raise ValueError(f"Unknown advice_type: {kind}")

    def observe(self, offer: Offer, sold: bool) -> None:
        bucket = self._price_bucket(offer.markup)
        counts = self.price_outcomes.setdefault((offer.quality, bucket), [0, 0])
        counts[0 if sold else 1] += 1
        if self.kind == "margin_feedback":
            if sold:
                self.target_markup = min(
                    float(self.settings.get("max_markup", 0.30)),
                    self.target_markup + float(self.settings.get("step_up", 0.015)),
                )
            else:
                self.target_markup = max(
                    float(self.settings.get("min_markup", -0.10)),
                    self.target_markup - float(self.settings.get("step_down", 0.025)),
                )

    @staticmethod
    def _price_bucket(markup: float) -> int:
        return round(markup / 0.05)

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return min(high, max(low, value))


class SellerAgent:
    """Chooses a batch and executes the price recommendation from its adviser."""

    def __init__(self, name: str, settings: dict):
        self.name = name
        self.settings = settings
        self.adviser = AdviceAgent(settings)
        self.last_offer: Offer | None = None

    def make_offer(
        self,
        inventory: list[dict],
        competitor_offers: list[Offer],
        rounds_left: int,
        total_rounds: int,
    ) -> Offer | None:
        available = [batch for batch in inventory if batch["quantity"] > 0]
        if not available:
            self.last_offer = None
            return None
        inventory_units = sum(batch["quantity"] for batch in available)
        initial_inventory = int(self.settings.get("initial_inventory_units", inventory_units))
        choices = []
        for batch in available:
            markup = self.adviser.recommend(
                batch, competitor_offers, inventory_units, initial_inventory,
                rounds_left, total_rounds,
            )
            # Select the offer with the largest immediate markup value.
            expected_surplus = batch["reference_price"] * markup
            choices.append((expected_surplus, batch, markup))
        _, batch, markup = max(choices, key=lambda item: item[0])
        offer = Offer(
            seller=self.name,
            batch_id=batch["id"],
            quality=batch["quality"],
            reference_price=batch["reference_price"],
            ask_price=round(batch["reference_price"] * (1 + markup), 2),
            markup=markup,
        )
        self.last_offer = offer
        return offer

    def observe(self, sold: bool) -> None:
        if self.last_offer is not None:
            self.adviser.observe(self.last_offer, sold)

