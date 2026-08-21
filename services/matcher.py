import asyncio
from typing import Any

import structlog
from rapidfuzz import fuzz, process, utils

log = structlog.get_logger()

CACHE_TTL = 3600  # seconds
SEARCH_THRESHOLD = 55
TOP_N = 20  # fetch more, UI paginates by PAGE_SIZE
PAGE_SIZE = 5


class IngredientMatcher:
    def __init__(self) -> None:
        # client_id -> list of ingredient dicts
        self._cache: dict[int, list[dict]] = {}
        self._last_update: dict[int, float] = {}

    def update_catalog(self, client_id: int, ingredients: list[dict]) -> None:
        self._cache[client_id] = ingredients
        self._last_update[client_id] = asyncio.get_event_loop().time()
        log.info("matcher.catalog_updated", client_id=client_id, count=len(ingredients))

    def is_stale(self, client_id: int) -> bool:
        if client_id not in self._last_update:
            return True
        elapsed = asyncio.get_event_loop().time() - self._last_update[client_id]
        return elapsed > CACHE_TTL

    def invalidate(self, client_id: int) -> None:
        """Force the next is_stale() to report True — used by the manual refresh."""
        self._last_update.pop(client_id, None)

    def search(self, query: str, client_id: int) -> list[dict]:
        ingredients = self._cache.get(client_id, [])
        if not ingredients:
            return []

        # Key choices by LIST INDEX, not ingredient_id. Poster's ingredient_id
        # (storage ingredients) and product_id (menu products/dishes) are separate
        # ID spaces that overlap numerically — e.g. id 2066 is both "Ожина" the
        # ingredient and a beer product. Keying by ingredient_id collapsed such
        # collisions, silently hiding items from search (Ожина was unfindable and
        # the шт "Кукурудза" was masked by a kg dish of the same name).
        choices = {i: item["ingredient_name"] for i, item in enumerate(ingredients)}
        results = process.extract(
            query,
            choices,
            scorer=fuzz.token_set_ratio,
            # default_process, NOT str.lower. Both lowercase (goods are Capitalised
            # in Poster, so "футо" would score 0 against "Футо з лососем"), but
            # str.lower leaves punctuation glued to tokens — and Poster names quote
            # the distinctive part: 'Пиво "Золотий ель" непастеризоване 0,33л'
            # tokenises to '"золотий', 'ель"', which intersects an ordinary query
            # at zero tokens. token_set_ratio then degrades to comparing a short
            # query against a 55-char string: 'золотий ель' scored 27 (cutoff 55),
            # so NO beer was findable by name. default_process replaces every
            # non-alphanumeric with a space → same query scores 100, rank 1.
            processor=utils.default_process,
            limit=TOP_N,
            score_cutoff=SEARCH_THRESHOLD,
        )
        # Preserve score order; each result maps to a distinct catalog entry.
        return [ingredients[idx] for _, _, idx in results]

    def get_by_id(self, ingredient_id: int, client_id: int) -> dict | None:
        for item in self._cache.get(client_id, []):
            if item["ingredient_id"] == ingredient_id:
                return item
        return None


# Global singleton
matcher = IngredientMatcher()


# Poster stores units as internal codes; show human Ukrainian labels instead.
_UNIT_LABELS = {"kg": "кг", "l": "л", "p": "шт", "pcs": "шт", "pc": "шт", " шт": "шт"}


def _unit_label(raw_unit: str | None) -> str:
    u = (raw_unit or "").strip()
    return _UNIT_LABELS.get(u.lower(), u or "шт")


def parse_leftovers(raw: list[dict]) -> list[dict]:
    """Normalize storage.getStorageLeftovers → ingredients (item_type=4)."""
    result = []
    for item in raw:
        ingredient_id = item.get("ingredient_id") or item.get("id")
        name = item.get("ingredient_name") or item.get("name", "")
        unit = _unit_label(item.get("ingredient_unit") or item.get("unit"))
        if ingredient_id and name:
            result.append({
                "ingredient_id": int(ingredient_id),
                "ingredient_name": name,
                "unit": unit,
                "item_type": "4",
                "leftover": item.get("ingredient_left") or item.get("left", 0),
            })
    return result


def parse_products(raw: list[dict]) -> list[dict]:
    """Normalize menu.getProducts → products/dishes for write-off.

    IMPORTANT: menu.getProducts `type` is a DIFFERENT enum from the one
    storage.createWriteOff expects, so it must be translated, not passed through:
      getProducts:      2 = страва/тех-карта (ingredient_id=0)
                        3 = товар (resale goods, backed by ingredient_id)
      createWriteOff:   1 = товар, 2 = страва, 3 = напівфабрикат(prepack), 4 = інгредієнт
    Passing getProducts type=3 straight through made Poster treat a товар as a
    prepack and reject the write-off with error 32 ("prepack_id is undefined").
    Verified on a live account: товар writes off correctly as type=1 by product_id.
    """
    result = []
    for item in raw:
        product_id = item.get("product_id") or item.get("id")
        name = item.get("product_name") or item.get("name", "")
        gp_type = str(item.get("type", ""))
        writeoff_type = "2" if gp_type == "2" else "1"  # dish → 2, everything else → product(1)
        # Unit comes from `weight_flag`, NOT the `unit` field: for a dish the
        # `unit` field is "kg" (its recipe/cost basis) even though it's sold and
        # written off by the portion. weight_flag 0 = counted by piece (шт),
        # 1 = weighed (кг/л). This is why "Капучино 250 мл" showed кг instead of шт.
        if str(item.get("weight_flag", "0")) == "1":
            raw_unit = item.get("unit")
            unit = _unit_label(raw_unit) if raw_unit else "кг"
        else:
            unit = "шт"
        if product_id and name:
            result.append({
                "ingredient_id": int(product_id),
                "ingredient_name": name,
                "unit": unit,
                "item_type": writeoff_type,
            })
    return result


def parse_modificators(raw: list[dict]) -> list[dict]:
    """Normalize menu.getModificators → modificators (item_type=5)."""
    result = []
    for item in raw:
        mod_id = item.get("modificator_id") or item.get("id")
        name = item.get("modificator_name") or item.get("name", "")
        unit = _unit_label(item.get("unit"))
        if mod_id and name:
            result.append({
                "ingredient_id": int(mod_id),
                "ingredient_name": name,
                "unit": unit,
                "item_type": "5",
            })
    return result


def build_catalog(
    leftovers: list[dict],
    products: list[dict],
    modificators: list[dict],
) -> list[dict]:
    """Merge all sources, deduplicate by (item_type, ingredient_id)."""
    seen: set[tuple] = set()
    catalog: list[dict] = []
    for item in [*leftovers, *products, *modificators]:
        key = (item["item_type"], item["ingredient_id"])
        if key not in seen:
            seen.add(key)
            catalog.append(item)
    return catalog


class CatalogRefreshError(Exception):
    """An essential catalog source failed — the cached catalog was left untouched."""


async def refresh_catalog(client_id: int, access_token: str) -> int:
    """Fetch every catalog source and replace the cached catalog for this client.

    Leftovers and products are ESSENTIAL: if either fails, we raise and keep the
    previous catalog together with its timestamp. Overwriting on a partial fetch
    used to wipe every dish and product from search for a full TTL — the caller
    reset the freshness timer regardless of what came back, so a single blip hid
    all 850+ menu items for an hour.

    Modificators are optional on purpose: menu.getModificators does not exist
    (HTTP 405 on every account), so treating it as essential would mean the
    catalog could never refresh at all.

    Returns the number of catalog entries.
    """
    from services.poster import PosterClient  # local import: avoids an import cycle

    poster = PosterClient(access_token)
    try:
        raw_leftovers, raw_products, raw_mods = await asyncio.gather(
            poster.get_storage_leftovers(),
            poster.get_products(),
            poster.get_modificators(),
            return_exceptions=True,
        )
    finally:
        await poster.aclose()

    failed = [
        name
        for name, result in (("leftovers", raw_leftovers), ("products", raw_products))
        if not isinstance(result, list)
    ]
    if failed:
        errors = {
            "leftovers": raw_leftovers if not isinstance(raw_leftovers, list) else None,
            "products": raw_products if not isinstance(raw_products, list) else None,
        }
        log.warning(
            "matcher.refresh_failed",
            client_id=client_id,
            sources=failed,
            error=str(errors[failed[0]]),
            kept_entries=len(matcher._cache.get(client_id, [])),
        )
        raise CatalogRefreshError(f"Poster не відповів: {', '.join(failed)}")

    catalog = build_catalog(
        parse_leftovers(raw_leftovers),
        parse_products(raw_products),
        parse_modificators(raw_mods if isinstance(raw_mods, list) else []),
    )
    matcher.update_catalog(client_id, catalog)
    return len(catalog)
