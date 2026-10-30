import json
import store
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("products")


@mcp.tool()
def list_products(category: str = "") -> str:
    """List products with stock and prices."""
    prods = store.list_products()
    if category:
        prods = [p for p in prods if p["category"].lower() == category.lower()]
    return json.dumps(prods)


@mcp.tool()
def get_product(product_id: str) -> str:
    """Get product specifications and pricing."""
    p = store.load_db()["products"].get(product_id.upper())
    return json.dumps(p) if p else json.dumps({"error": "Product not found"})


@mcp.tool()
def check_stock(product_id: str) -> str:
    """Check inventory levels for product."""
    p = store.load_db()["products"].get(product_id.upper())
    return json.dumps({"id": p["id"], "stock": p["stock"]}) if p else json.dumps({"error": "Product not found"})


@mcp.tool()
def get_supplier_info(product_id: str) -> str:
    """Get supplier details and lead times."""
    p = store.load_db()["products"].get(product_id.upper())
    return json.dumps(p["supplier"]) if p and "supplier" in p else json.dumps({"error": "No supplier info"})


if __name__ == "__main__":
    mcp.run(transport="stdio")