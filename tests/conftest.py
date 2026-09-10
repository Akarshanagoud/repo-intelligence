"""A synthetic repository with known, deliberately planted properties.

Every fixture below exists so a test can assert an exact number rather than
"looks about right": a known import cycle, a known complexity, a known
duplicate block, a known undocumented function.
"""
from __future__ import annotations

from pathlib import Path

import pytest

FILES: dict[str, str] = {
    # --- a clean layered package ---------------------------------------------
    "src/shop/__init__.py": '"""Shop package."""\n',
    "src/shop/models.py": '''"""Domain models for the shop."""


class Product:
    """A thing for sale."""

    def __init__(self, sku: str, price: float) -> None:
        self.sku = sku
        self.price = price

    def discounted(self, percent: float) -> float:
        """Price after a percentage discount."""
        if percent < 0:
            raise ValueError("negative discount")
        if percent > 100:
            raise ValueError("discount over 100%")
        return self.price * (1 - percent / 100)


MAX_BASKET_SIZE = 50
''',
    "src/shop/repository.py": '''"""Data access for products."""

from shop.models import Product


class ProductRepository:
    """Loads products."""

    def __init__(self, rows):
        self.rows = rows

    def get(self, sku):
        for row in self.rows:
            if row["sku"] == sku:
                return Product(row["sku"], row["price"])
        return None
''',
    # A deliberately complex, undocumented function.
    "src/shop/pricing.py": '''"""Pricing rules."""

from shop.models import Product
from shop.repository import ProductRepository


def calculate_total(items, member, coupon, region, express):
    total = 0.0
    for item in items:
        if item is None:
            continue
        if member and item.price > 100:
            total += item.price * 0.9
        elif member:
            total += item.price * 0.95
        elif coupon and item.price > 50:
            total += item.price * 0.97
        else:
            total += item.price
    if region == "EU" or region == "UK":
        total *= 1.2
    elif region == "US":
        total *= 1.07
    if express and total < 100:
        total += 15
    elif express:
        total += 5
    return total


def apply_bulk_discount(items):
    """Discount for large baskets."""
    if len(items) > 10:
        return 0.9
    return 1.0
''',
    # --- a deliberate two-module import cycle --------------------------------
    "src/shop/orders.py": '''"""Order handling. Imports billing, which imports back."""

from shop.billing import charge


def place_order(order):
    """Place an order and charge for it."""
    return charge(order)
''',
    "src/shop/billing.py": '''"""Billing. Imports orders, closing the cycle."""

from shop.orders import place_order


def charge(order):
    """Charge for an order."""
    return order.total


def retry_order(order):
    """Retry via orders."""
    return place_order(order)
''',
    # --- duplicated block across two files ------------------------------------
    "src/shop/report_a.py": '''"""Report A."""


def build_report(rows):
    """Build a report."""
    totals = {}
    for row in rows:
        key = row["category"]
        totals.setdefault(key, 0.0)
        totals[key] += row["amount"]
        if totals[key] > 1000:
            totals[key] = round(totals[key], 2)
    return totals
''',
    "src/shop/report_b.py": '''"""Report B, containing a copy-pasted block."""


def build_summary(rows):
    """Build a summary."""
    totals = {}
    for row in rows:
        key = row["category"]
        totals.setdefault(key, 0.0)
        totals[key] += row["amount"]
        if totals[key] > 1000:
            totals[key] = round(totals[key], 2)
    return totals
''',
    # --- a file that does not parse ------------------------------------------
    "src/shop/broken.py": "def unclosed(:\n    pass\n",
    # --- other languages -------------------------------------------------------
    "web/api.ts": """import { Product } from './models';

export interface CartLine {
  sku: string;
  quantity: number;
}

export class CartService {
  addLine(line: CartLine) {
    if (line.quantity < 1) {
      throw new Error('bad quantity');
    }
    return line;
  }
}

export function validateAuthToken(token: string): boolean {
  if (!token) {
    return false;
  }
  return token.length > 20;
}
""",
    "web/models.ts": """export interface Product {
  sku: string;
  price: number;
}
""",
    "service/handler.go": """package service

import (
	"errors"
)

type Handler struct {
	Name string
}

func NewHandler(name string) *Handler {
	return &Handler{Name: name}
}

func (h *Handler) Process(input string) error {
	if input == "" {
		return errors.New("empty")
	}
	return nil
}
""",
    # --- tests ----------------------------------------------------------------
    "tests/test_pricing.py": '''"""Tests for pricing."""

from shop.pricing import calculate_total


def test_calculate_total_with_member():
    assert calculate_total([], True, False, "US", False) == 0.0


def test_repository_lookup():
    from shop.repository import ProductRepository

    assert ProductRepository([]).get("x") is None
''',
    # --- things that must be excluded -----------------------------------------
    ".gitignore": "ignored_dir/\n*.ignored.py\nbuild/\n",
    "ignored_dir/secret.py": "SECRET = 1\n",
    "keepout.ignored.py": "IGNORED = 1\n",
    "node_modules/pkg/index.js": "module.exports = {};\n",
    "build/artifact.py": "BUILT = 1\n",
    "assets/bundle.min.js": "var a=1;" * 300,
    "docs/README.md": "# Docs\n\nSome documentation.\n",
    "pyproject.toml": """[project]
name = "shop"
version = "0.1.0"

[project.urls]
Homepage = "https://example.com/shop"

[project.scripts]
shop = "shop.cli:main"
""",
}


@pytest.fixture(scope="session")
def sample_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("sample_repo")
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # A generated file detected by its header marker rather than its name.
    (root / "src" / "shop" / "schema_generated.py").write_text(
        "# @generated by a tool. DO NOT EDIT.\nX = 1\n", encoding="utf-8"
    )
    # A binary file, detected by content.
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "logo.py").write_bytes(b"\x00\x01\x02binary")
    return root


@pytest.fixture(scope="session")
def analysis(sample_repo):
    from repointel import AnalysisConfig, analyze

    # git churn is disabled: the fixture is not a git repository, and the point
    # is to exercise the "churn unavailable" path deliberately.
    return analyze(sample_repo, AnalysisConfig(use_git_churn=False))
