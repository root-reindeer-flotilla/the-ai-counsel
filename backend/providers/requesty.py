"""Requesty.ai provider wrapper."""

import httpx
from typing import List, Dict, Any
from .base import LLMProvider
from .. import requesty

REQUESTY_MODELS_URL = "https://router.requesty.ai/v1/models"


class RequestyProvider(LLMProvider):
    """Requesty.ai API provider."""

    async def query(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        timeout: float = 120.0,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        if model_id.startswith("requesty:"):
            model_id = model_id.replace("requesty:", "", 1)
        return await requesty.query_model(model_id, messages, timeout, temperature)

    async def get_models(self) -> List[Dict[str, Any]]:
        models = await requesty.fetch_models()
        for m in models:
            raw_id = m["id"]
            m["id"] = f"requesty:{raw_id}"
            m["name"] = (m.get("name") or raw_id) + " [Requesty]"
        return sorted(models, key=lambda x: (x.get("name") or "").lower())

    async def validate_key(self, api_key: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    REQUESTY_MODELS_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if response.status_code == 200:
                    return {"success": True, "message": "API key is valid"}
                if response.status_code == 401:
                    return {"success": False, "message": "Invalid API key"}
                return {"success": False, "message": f"API error: {response.status_code}"}
        except Exception as e:
            return {"success": False, "message": str(e)}
