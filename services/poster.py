from datetime import datetime

import httpx
import structlog

log = structlog.get_logger()

POSTER_API_BASE = "https://joinposter.com/api"


class PosterError(Exception):
    pass


class PosterClient:
    def __init__(self, access_token: str) -> None:
        self._token = access_token
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _get(self, method: str, **params) -> dict:
        params["token"] = self._token
        resp = await self._client.get(f"{POSTER_API_BASE}/{method}", params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            raise PosterError(f"Poster API error: {data['error']}")
        return data

    async def _post(self, method: str, body: dict) -> dict:
        resp = await self._client.post(
            f"{POSTER_API_BASE}/{method}",
            params={"token": self._token},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            raise PosterError(f"Poster API error: {data['error']}")
        return data

    async def get_storages(self) -> list[dict]:
        data = await self._get("storage.getStorages")
        return data.get("response", [])

    async def get_products(self) -> list[dict]:
        """Fetch menu products and dishes (type 1 = product, type 2 = dish)."""
        data = await self._get("menu.getProducts")
        return data.get("response", [])

    async def get_modificators(self) -> list[dict]:
        """Fetch menu modificators (write-off type 5)."""
        data = await self._get("menu.getModificators")
        response = data.get("response", [])
        # response may be a dict {category_id: [items]}
        if isinstance(response, dict):
            items = []
            for v in response.values():
                if isinstance(v, list):
                    items.extend(v)
            return items
        return response

    async def get_storage_leftovers(self, storage_id: int | None = None) -> list[dict]:
        kwargs = {}
        if storage_id:
            kwargs["storage_id"] = storage_id
        data = await self._get("storage.getStorageLeftovers", **kwargs)
        response = data.get("response", [])
        if isinstance(response, dict):
            # Poster sometimes returns {storage_id: [...]} dict
            items = []
            for v in response.values():
                if isinstance(v, list):
                    items.extend(v)
            return items
        return response

    async def create_writeoff(
        self,
        storage_id: int,
        ingredients: list[dict],
        reason: str = "Списання",
        reason_id: int | None = None,
    ) -> str:
        """
        Official format:
        {
          "write_off": {"storage_id": "1", "date": "Y-m-d H:i:s"},
          "ingredient": [{"id": "9", "type": "4", "weight": "0.1", "reason": "..."}]
        }
        type: 1=product, 2=dish, 3=prepack, 4=ingredient, 5=modifier
        """
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_off_obj: dict = {"storage_id": str(storage_id), "date": date_str}
        if reason_id is not None:
            write_off_obj["reason_id"] = str(reason_id)
        body = {
            "write_off": write_off_obj,
            "ingredient": [
                {
                    "id": str(item["ingredient_id"]),
                    "type": str(item.get("item_type", "4")),
                    "weight": str(item["amount"]),
                    "reason": reason,
                }
                for item in ingredients
            ],
        }
        data = await self._post("storage.createWriteOff", body)
        if data.get("success") != 1:
            raise PosterError(f"createWriteOff failed: {data}")
        writeoff_id = str(data.get("response", ""))
        log.info("poster.create_writeoff", writeoff_id=writeoff_id)
        return writeoff_id

    async def get_writeoff_reasons(self) -> list[dict]:
        """Fetch write-off reasons from Poster.

        The method is `storage.getWasteReasons` — NOT `getWriteOffReasons`, which
        doesn't exist and returns "Method Not Allowed" (code 30). Each item looks
        like {"reason_id": 1, "name": "порча", "pnl_group": 3, "delete": 0}; we
        skip soft-deleted ones (delete != 0).
        """
        try:
            data = await self._get("storage.getWasteReasons")
            response = data.get("response", [])
            if isinstance(response, dict):  # some Poster versions return a dict
                response = list(response.values())
            if isinstance(response, list):
                return [r for r in response if isinstance(r, dict) and str(r.get("delete", "0")) in ("0", "")]
        except Exception:
            pass
        return []

    async def aclose(self) -> None:
        await self._client.aclose()
