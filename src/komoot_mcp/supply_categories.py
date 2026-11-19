"""Verified Komoot map-POI categories, not custom/import waypoint categories.

These are opportunities, not guarantees of availability. A spring does not imply
potable water; a hut does not imply staffing or food service. Ornamental fountains
(148), animal watering places (244), generic shops and accommodation are excluded.
"""

CATEGORY_SOURCE: dict[str, str] = {
    "url": "https://www.komoot.com/assets/13477.035ef7ab062b72c0.js",
    "module": "52306: common/categories",
    "sha256": "b34d024cfd026741776e3bfbf77ee0c257df8fbe4166fe72164dd01855ed4d97",
    "corroborating_url": "https://www.komoot.com/assets/33952.12083cae9e4dcc12.js",
    "corroborating_module": "41376: common/categories (de)",
    "binding_url": "https://www.komoot.com/assets/92008.db5b6a294268a635.js",
    "binding_module": "88949: POI search category -> common/categories c<number>",
    "date": "2026-11-19",
}

# Stable briefing labels; map labels below preserve the more specific meaning.
SUPPLY_CATEGORIES: dict[int, str] = {
    9: "food",  # Alpine Hut: service is not established by the category.
    29: "food",
    40: "food",
    41: "food",
    45: "food",
    46: "food",
    61: "food",
    62: "food",
    69: "groceries",
    73: "toilets",
    80: "groceries",
    81: "groceries",
    82: "groceries",
    83: "groceries",
    86: "groceries",
    88: "groceries",
    89: "groceries",
    120: "water",
    121: "groceries",
    122: "groceries",
    164: "water",
}

CATEGORY_LABELS: dict[int, str] = {
    9: "Alpine Hut",
    29: "Beer Garden",
    40: "Restaurant",
    41: "Café",
    45: "Food Court",
    46: "Fast Food",
    61: "Pub",
    62: "Bar",
    69: "Farm Shop",
    73: "Public Toilet",
    80: "Bakery",
    81: "Butcher",
    82: "Confectionery",
    83: "Convenience Store",
    86: "Kiosk",
    88: "Organic Grocery Store",
    89: "Supermarket",
    120: "Spring",
    121: "Beverage Store",
    122: "Greengrocer",
    164: "Public Water Fountain",
}

# All numeric keys present in the verified map-category translation namespace.
# Missing IDs must remain unknown, rather than silently treated as non-supplies.
KNOWN_CATEGORIES: frozenset[int] = frozenset(
    (
        *range(189),
        *range(190, 193),
        194,
        214,
        *range(216, 235),
        *range(243, 247),
        *range(300, 328),
    )
)
