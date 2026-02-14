"""
AI-модерация через Perplexity API.
"""

import json
import aiohttp
from config import PERPLEXITY_API_KEY, PERPLEXITY_MODEL

SYSTEM_PROMPT = """Ты — система модерации чата. Анализируй сообщение и определи нарушение.

Правила:
1. Запрещены оскорбления, угрозы, буллинг
2. Запрещён спам, реклама, ссылки без разрешения
3. Запрещена дискриминация
4. Запрещён NSFW контент
5. Запрещены призывы к насилию
6. Запрещён доксинг

Ответь строго JSON без markdown:
{"violation": true/false, "severity": "none"|"low"|"medium"|"high"|"critical", "action": "none"|"warn"|"mute"|"ban", "reason": "описание на русском"}"""


async def analyze_message(text: str) -> dict | None:
    if not PERPLEXITY_API_KEY:
        return None

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Проанализируй:\n\n{text}"},
        ],
        "max_tokens": 300,
        "temperature": 0.1,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[-1]
                if content.endswith("```"):
                    content = content.rsplit("```", 1)[0]
                return json.loads(content.strip())
    except Exception:
        return None
