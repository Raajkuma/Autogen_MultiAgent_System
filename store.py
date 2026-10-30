"""
Centralized JSON storage engine enforcing thread safety, input sanitization,
and multi-tier refund/warranty business policies with human review flags.
"""
import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.getenv("SUPPORT_DB", Path(__file__).parent / "data" / "db.json"))
CUSTOMERS = ["Priya", "Arjun", "Meena", "Raajkumar"]
_lock = threading.RLock()


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def days_since(order: dict) -> int:
    return (date.today() - date.fromisoformat(order["date"])).days


def _event(order: dict, actor: str, text: str) -> None:
    order.setdefault("events", []).append({"ts": _now(), "actor": actor, "text": text})


def _seed() -> dict:
    products = {
        "P100": {
            "id": "P100", "name": "AeroBook 14 Laptop", "category": "laptops", "price": 899.0,
            "stock": 42, "specs": "14in FHD, 16GB RAM, 512GB SSD, 12h battery", "warranty_months": 24,
            "supplier": {"name": "TechSource Ltd", "lead_time_days": 7, "min_order_qty": 10},
        },
        "P200": {
            "id": "P200", "name": "NovaPhone X", "category": "phones", "price": 649.0,
            "stock": 0, "specs": "6.5in OLED, 128GB, 5G, dual camera", "warranty_months": 12,
            "supplier": {"name": "MobiWorld Inc", "lead_time_days": 14, "min_order_qty": 25},
        },
        "P300": {
            "id": "P300", "name": "PulseBuds Pro", "category": "audio", "price": 129.0,
            "stock": 310, "specs": "ANC, 30h battery with case, Bluetooth 5.3", "warranty_months": 12,
            "supplier": {"name": "SoundHub Co", "lead_time_days": 5, "min_order_qty": 50},
        },
        "P400": {
            "id": "P400", "name": "ViewMax 27 Monitor", "category": "monitors", "price": 279.0,
            "stock": 18, "specs": "27in QHD IPS, 144Hz, USB-C 65W", "warranty_months": 36,
            "supplier": {"name": "TechSource Ltd", "lead_time_days": 10, "min_order_qty": 5},
        },
    }

    def create_order(oid, customer, pid, qty, days_ago, status, refund="none", payment="paid", human_review="None"):
        p = products[pid]
        o = {
            "id": oid, "customer": customer, "date": _d(days_ago), "product_id": pid,
            "product": p["name"], "price": p["price"], "qty": qty,
            "total": round(p["price"] * qty, 2), "status": status,
            "payment_status": payment, "refund_status": refund, "human_review": human_review, "events": [],
        }
        _event(o, "Shop UI", f"Order placed ({qty} x {p['name']})")
        if status in ("shipped", "delivered"):
            _event(o, "Warehouse", "Marked shipped")
        if status == "delivered":
            _event(o, "Courier", "Marked delivered")
        return o

    orders = {
        o["id"]: o
        for o in [
            create_order("ORD-1001", "Priya", "P100", 1, 10, "delivered"),
            create_order("ORD-1007", "Raajkumar", "P100", 1, 5, "shipped"),
            create_order("ORD-1008", "Raajkumar", "P100", 1, 40, "delivered"),
        ]
    }

    policies = [
        {
            "id": "refund",
            "title": "Comprehensive Refund & Service Policy",
            "keywords": "refund return money back defective warranty months days",
            "text": "1. Within 30 days of delivery: Eligible for 100% full refund.\n"
                    "2. Within 3 months (30 to 90 days): Eligible for 25% partial refund only.\n"
                    "3. More than 3 months: No refunds, but active warranty covers doorstep technician assignment.",
        },
        {
            "id": "cancel",
            "title": "Cancellation policy",
            "keywords": "cancel cancellation stop order",
            "text": "Orders can be cancelled only before shipment (status 'placed'). Shipped or delivered orders must undergo human review and approval.",
        },
    ]

    return {
        "products": products,
        "orders": orders,
        "approvals": {},
        "tickets": {},
        "sales_history": [],
        "policies": policies,
        "it_kb": [],
        "next_order": 1009,
        "next_approval": 1,
        "next_ticket": 1,
    }


def save_db(db: dict) -> None:
    with _lock:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = DB_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(db, indent=2))
        os.replace(tmp, DB_PATH)


def load_db() -> dict:
    with _lock:
        if not DB_PATH.exists():
            save_db(_seed())
        return json.loads(DB_PATH.read_text())


def reset_db() -> None:
    save_db(_seed())


def list_products() -> list[dict]:
    return list(load_db()["products"].values())


def list_orders(customer: str | None = None) -> list[dict]:
    orders = load_db()["orders"].values()
    return [o for o in orders if not customer or o["customer"] == customer]


def get_order(order_id: str) -> dict | None:
    if not isinstance(order_id, str):
        if type(order_id).__name__ == "ModelMetaclass" or hasattr(order_id, "__mro__"):
            return None
        order_id = str(order_id)
    return load_db()["orders"].get(order_id.upper())


def list_approvals(status: str | None = None) -> list[dict]:
    """Filters orders where human_review is Pending or matches requested status."""
    db = load_db()
    orders = db["orders"].values()
    if status and status.lower() == "pending":
        return [o for o in orders if o.get("human_review") == "Pending"]
    return [o for o in orders if o.get("human_review") and o.get("human_review") != "None"]


def search_policy(query: str) -> list[dict]:
    return load_db()["policies"]


def place_order(customer: str, product_id: str, qty: int, actor: str = "Shop UI") -> dict:
    with _lock:
        db = load_db()
        p = db["products"].get(str(product_id).upper())
        if not p:
            return {"ok": False, "error": f"Unknown product '{product_id}'"}
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Quantity must be a valid integer."}

        if qty < 1 or p["stock"] < qty:
            return {"ok": False, "error": f"Insufficient stock ({p['stock']} available)"}

        p["stock"] -= qty
        oid = f"ORD-{db['next_order']}"
        db["next_order"] += 1

        o = {
            "id": oid,
            "customer": customer,
            "date": date.today().isoformat(),
            "product_id": p["id"],
            "product": p["name"],
            "price": p["price"],
            "qty": qty,
            "total": round(p["price"] * qty, 2),
            "status": "placed",
            "payment_status": "paid",
            "refund_status": "none",
            "human_review": "None",
            "events": [],
        }
        _event(o, actor, f"Order placed ({qty} x {p['name']})")
        db["orders"][oid] = o
        save_db(db)
        return {"ok": True, "order": o}


def evaluate_refund_policy(order_id: str, reason: str) -> dict:
    if not isinstance(order_id, str):
        if type(order_id).__name__ == "ModelMetaclass" or hasattr(order_id, "__mro__"):
            return {"ok": False, "error": "Invalid order identifier provided."}
        order_id = str(order_id)
    if not isinstance(reason, str):
        reason = str(reason)

    with _lock:
        db = load_db()
        o = db["orders"].get(order_id.upper())
        if not o:
            return {"ok": False, "error": f"Order {order_id} not found."}

        days = days_since(o)
        p = db["products"].get(o["product_id"], {})
        warranty_months = p.get("warranty_months", 12)
        warranty_days = warranty_months * 30

        if days <= 30:
            eligible_amount = float(o["total"])
            tier = "100% Full Refund (Within 30 days)"
        elif days <= 90:
            eligible_amount = round(float(o["total"]) * 0.25, 2)
            tier = "25% Partial Refund (Within 3 months)"
        elif days <= warranty_days:
            tier = "Doorstep Service Technician Assignment (Active Warranty)"
            eligible_amount = 0.0
        else:
            return {"ok": False, "error": f"Order is {days} days old and outside its warranty period."}

        # Set human review flag to Pending and log cancellation/refund request event
        o["human_review"] = "Pending"
        _event(o, "Payment_Agent", f"Order cancellation & refund requested ({tier}). Human review flag set to Pending. Reason: {reason}")
        save_db(db)
        return {"ok": True, "tier": tier, "amount": eligible_amount, "order_id": o["id"]}


def cancel_order(order_id: str, actor: str = "Shop UI") -> dict:
    if not isinstance(order_id, str):
        if type(order_id).__name__ == "ModelMetaclass" or hasattr(order_id, "__mro__"):
            return {"ok": False, "error": "Invalid order identifier provided."}
        order_id = str(order_id)

    with _lock:
        db = load_db()
        o = db["orders"].get(order_id.upper())
        if not o:
            return {"ok": False, "error": f"Order {order_id} not found."}

        # If order is shipped or delivered, automatically queue for human review
        if o["status"] != "placed":
            return evaluate_refund_policy(order_id, reason=f"User requested cancellation on a {o['status']} order.")

        o["status"] = "cancelled"
        o["payment_status"] = "refunded"
        o["refund_status"] = "refunded"
        o["human_review"] = "Approved"
        if o["product_id"] in db["products"]:
            db["products"][o["product_id"]]["stock"] += o["qty"]

        _event(o, actor, "Order cancelled. 100% auto-refund issued and stock restocked.")
        save_db(db)
        return {"ok": True, "order": o}


def decide_order_approval(order_id: str, approve: bool, reviewer: str = "Human Reviewer", note: str = "") -> dict:
    """Processes approval or rejection for requested order cancellations/refunds."""
    with _lock:
        db = load_db()
        o = db["orders"].get(order_id.upper())
        if not o or o.get("human_review") != "Pending":
            return {"ok": False, "error": "Order request not found or not pending review."}

        if approve:
            o["human_review"] = "Approved"
            o["status"] = "cancelled"
            o["payment_status"] = "refunded"
            o["refund_status"] = "refunded"
            if o["product_id"] in db["products"]:
                db["products"][o["product_id"]]["stock"] += o["qty"]
            _event(o, reviewer, f"Cancellation/Refund APPROVED. Remarks: {note if note else 'None'}. Refund paid out.")
        else:
            o["human_review"] = "Rejected"
            _event(o, reviewer, f"Cancellation/Refund REJECTED. Remarks: {note if note else 'None'}.")

        save_db(db)
        return {"ok": True, "order": o}