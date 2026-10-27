"""Multi-seller practice market with capped demand and terminal liquidation."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from agent import Offer, SellerAgent

ADVICE_LABELS_TH = {
    "expected_payoff": "เลือกผลตอบแทนคาดหวังสูงสุด",
    "competitor_undercut": "ตั้งราคาต่ำกว่าคู่แข่ง",
    "inventory_clearance": "เร่งระบายสต็อก",
    "margin_feedback": "ปรับมาร์จินจากผลตอบรับ",
}
QUALITY_LABELS_TH = {
    "standard": "มาตรฐาน",
    "premium": "พรีเมียม",
    "specialty": "พิเศษ",
}


@dataclass
class Seller:
    name: str
    inventory: list[dict]
    agent: SellerAgent
    initial_reference_value: float
    sales: int = 0
    sale_revenue: float = 0.0
    liquidation_revenue: float = 0.0
    liquidated_units: int = 0

    @property
    def inventory_units(self) -> int:
        return sum(batch["quantity"] for batch in self.inventory)

    def remove_unit(self, batch_id: str, price: float) -> None:
        batch = next(item for item in self.inventory if item["id"] == batch_id)
        batch["quantity"] -= 1
        self.sales += 1
        self.sale_revenue += price


def load_config(config_path: str | Path) -> dict:
    with open(config_path, encoding="utf-8") as stream:
        config = json.load(stream)
    if not config.get("teams"):
        raise ValueError("Config must contain at least one team")
    names = [team.get("name") for team in config["teams"]]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("Team names must be present and unique")
    if any(team.get("advice_type") not in {
        "expected_payoff", "competitor_undercut", "inventory_clearance", "margin_feedback"
    } for team in config["teams"]):
        raise ValueError("A team has an unsupported advice_type")
    return config


def make_inventory(rng: random.Random, prefix: str, settings: dict) -> list[dict]:
    qualities = settings.get("inventory", [
        {"quality": "standard", "base_price": 100, "quantity": 2},
        {"quality": "premium", "base_price": 150, "quantity": 2},
        {"quality": "specialty", "base_price": 220, "quantity": 1},
    ])
    inventory = []
    for index, item in enumerate(qualities, start=1):
        inventory.append({
            "id": f"{prefix}-{index}",
            "quality": item["quality"],
            "reference_price": round(item["base_price"] * rng.uniform(0.90, 1.10), 2),
            "quantity": int(item["quantity"]),
        })
    return inventory


def _result_for(seller: Seller) -> dict:
    reference = seller.initial_reference_value
    total = seller.sale_revenue + seller.liquidation_revenue
    return {
        "team": seller.name,
        "advice_type": seller.agent.adviser.kind,
        "advice_label_th": ADVICE_LABELS_TH[seller.agent.adviser.kind],
        "initial_supply": sum(item["quantity"] for item in seller.inventory) + seller.sales + seller.liquidated_units,
        "sold_in_market": seller.sales,
        "forced_liquidation_units": seller.liquidated_units,
        "leftover_units_after_liquidation": seller.inventory_units,
        "sale_revenue": round(seller.sale_revenue, 2),
        "forced_liquidation_revenue": round(seller.liquidation_revenue, 2),
        "total_revenue": round(total, 2),
        "margin_vs_reference_proxy": round((total - reference) / reference, 4) if reference else 0.0,
        "final_target_markup": round(seller.agent.adviser.target_markup, 4),
    }


def run_game(
    rounds: int = 12,
    seed: int = 7,
    verbose: bool = True,
    config_path: str | Path | None = None,
) -> dict:
    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    if config_path is None:
        config_path = Path(__file__).with_name("agent_config.json")
    config = load_config(config_path)
    rng = random.Random(seed)
    sellers = []
    for team in config["teams"]:
        inventory = make_inventory(rng, team["name"], config)
        settings = dict(team)
        settings["initial_inventory_units"] = sum(item["quantity"] for item in inventory)
        initial_value = sum(item["reference_price"] * item["quantity"] for item in inventory)
        sellers.append(Seller(
            name=team["name"], inventory=inventory,
            agent=SellerAgent(team["name"], settings),
            initial_reference_value=initial_value,
        ))

    initial_supply = sum(seller.inventory_units for seller in sellers)
    demand_fraction = float(config.get("demand_fraction", 0.90))
    if not 0 <= demand_fraction <= 1:
        raise ValueError("demand_fraction must be between 0 and 1")
    total_demand_slots = round(initial_supply * demand_fraction)
    base_slots, extra_slots = divmod(total_demand_slots, rounds)
    reference_ceiling = float(config.get("buyer_max_markup", 0.18))
    liquidation_fraction = float(config.get("terminal_liquidation_fraction", 0.80))

    if verbose:
        print("ตลาดทดลอง Track 3")
        print(f"  จำนวนรอบ: {rounds}  |  Seed: {seed}")
        print(f"  อุปทานเริ่มต้น: {initial_supply} หน่วย")
        print(f"  เพดานอุปสงค์: {total_demand_slots} หน่วย ({demand_fraction:.0%})")
        print(f"  ปิดตลาด: บังคับขายสินค้าคงเหลือที่ {liquidation_fraction:.0%} ของราคากลาง")
        for seller in sellers:
            label = ADVICE_LABELS_TH[seller.agent.adviser.kind]
            print(f"  • {seller.name}: {label} | สต็อก {seller.inventory_units} หน่วย")
        print("\n" + "=" * 56)

    previous_offers: list[Offer] = []
    for round_index in range(rounds):
        slots = base_slots + (1 if round_index < extra_slots else 0)
        rounds_left = rounds - round_index
        round_offers: list[Offer] = []
        for seller in sellers:
            # Competitor-based advice uses the last observed market prices, so
            # decisions within the same round do not depend on seller order.
            competitors = [
                offer for offer in previous_offers
                if offer.seller != seller.name
                and any(
                    batch["id"] == offer.batch_id and batch["quantity"] > 0
                    for rival in sellers if rival.name == offer.seller
                    for batch in rival.inventory
                )
            ]
            offer = seller.agent.make_offer(seller.inventory, competitors, rounds_left, rounds)
            if offer:
                round_offers.append(offer)
        eligible = [
            offer for offer in round_offers
            if offer.ask_price <= offer.reference_price * (1 + reference_ceiling)
        ]
        winners = sorted(eligible, key=lambda offer: offer.ask_price)[:slots]
        winner_keys = {(offer.seller, offer.batch_id) for offer in winners}
        for offer in winners:
            seller = next(s for s in sellers if s.name == offer.seller)
            seller.remove_unit(offer.batch_id, offer.ask_price)
        for seller in sellers:
            offer = seller.agent.last_offer
            if offer is not None:
                sold = (offer.seller, offer.batch_id) in winner_keys
                seller.agent.observe(sold)
        previous_offers = round_offers

        if verbose:
            print(f"\nรอบที่ {round_index + 1}/{rounds}  |  ช่องซื้อ {slots} หน่วย")
            print("  ข้อเสนอ:")
            if round_offers:
                for offer in round_offers:
                    quality = QUALITY_LABELS_TH.get(offer.quality, offer.quality)
                    print(f"    • {offer.seller}: กาแฟ{quality} ราคา {offer.ask_price:,.2f} บาท")
            else:
                print("    • ไม่มีข้อเสนอ")
            print("  ผลการซื้อ:")
            if winners:
                for offer in winners:
                    print(f"    • {offer.seller} ขายได้ {offer.ask_price:,.2f} บาท")
            else:
                print("    • รอบนี้ไม่มีการขาย")

    for seller in sellers:
        for batch in seller.inventory:
            units = batch["quantity"]
            if units:
                seller.liquidated_units += units
                seller.liquidation_revenue += units * batch["reference_price"] * liquidation_fraction
                batch["quantity"] = 0

    results = [_result_for(seller) for seller in sellers]
    if verbose:
        print("\n" + "=" * 56)
        print("สรุปผลรายทีม")
        print("(มาร์จินเป็นค่าประมาณเทียบราคากลางรวม ไม่ใช่คะแนนทางการ)")
        for result in results:
            print(f"\n{result['team']} — {result['advice_label_th']}")
            print(f"  ขายในตลาด:       {result['sold_in_market']} หน่วย")
            print(f"  บังคับขายคงเหลือ: {result['forced_liquidation_units']} หน่วย")
            print(f"  รายรับจากตลาด:   {result['sale_revenue']:,.2f} บาท")
            print(f"  รายรับปิดสต็อก:  {result['forced_liquidation_revenue']:,.2f} บาท")
            print(f"  รายรับรวม:       {result['total_revenue']:,.2f} บาท")
            print(f"  มาร์จินประมาณ:   {result['margin_vs_reference_proxy']:.1%}")
    return {
        "rounds": rounds,
        "seed": seed,
        "initial_supply": initial_supply,
        "demand_cap": total_demand_slots,
        "demand_fraction": demand_fraction,
        "teams": results,
    }

