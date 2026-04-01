# main.py
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional
import uuid
import time
import asyncio
import os
import httpx

app = FastAPI(title="Roblox Voice Chat & Llama Guard Moderation")

# Разрешаем запросы из Roblox Studio и игр
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ключ от OpenRouter (или аналогичного сервиса для доступа к Llama Guard)
LLAMA_API_KEY = os.getenv("API_KEY", "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")

# Базовый список стоп-слов на случай, если API не ответит
BAD_WORDS_RU = ["хуй", "пизд", "бля", "сука", "ебал", "пидор", "гандон", "уеб", "шлюх", "залуп"]

# --- Модели данных ---
class VoiceMessage(BaseModel):
    sender_id: str
    room_id: str
    audio_data: str
    sequence: int

class PlayerRegistration(BaseModel):
    player_id: str
    player_name: str
    room_id: str

class ModerationRequest(BaseModel):
    text: str

active_players: Dict[str, Dict] = {}
voice_message_queue: Dict[str, List[Dict]] = {}

def cleanup_old_players():
    current_time = time.time()
    players_to_remove = [p_id for p_id, data in active_players.items() if current_time - data.get('last_seen', 0) > 30]
    for p_id in players_to_remove:
        active_players.pop(p_id, None)

# ==========================================
# ЭНДПОИНТ МОДЕРАЦИИ: LLAMA GUARD
# ==========================================
@app.post("/api/moderate")
async def moderate_text(request: ModerationRequest):
    # 1. Локальная проверка (быстро и бесплатно)
    text_lower = request.text.lower()
    for bad_word in BAD_WORDS_RU:
        if bad_word in text_lower:
            return {"status": "success", "flagged": True}

    # 2. Проверка через Llama Guard
    if LLAMA_API_KEY:
        # Используем OpenRouter как самый доступный шлюз к Llama Guard
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {LLAMA_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Llama Guard ждет текст и возвращает либо "safe", либо "unsafe"
        data = {
            "model": "meta-llama/llama-guard-3-8b",
            "messages": [{"role": "user", "content": request.text}]
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data, headers=headers, timeout=5.0)
                
                if response.status_code == 200:
                    result = response.json()
                    ai_response = result["choices"][0]["message"]["content"].strip().lower()
                    
                    # Llama Guard прямо пишет "unsafe", если сообщение нарушает правила
                    is_flagged = "unsafe" in ai_response
                    return {"status": "success", "flagged": is_flagged}
                
                print(f"[LLAMA ERROR] Код {response.status_code}: {response.text}")
                    
        except Exception as exc:
            print(f"[LLAMA ERROR] Ошибка сети: {exc}")
    
    # Если API упал или не настроен, но мата в списке нет — пропускаем
    return {"status": "success", "flagged": False}

# ==========================================
# ЭНДПОИНТЫ ГОЛОСОВОГО ЧАТА (БЕЗ ИЗМЕНЕНИЙ)
# ==========================================
@app.post("/api/register")
async def register_player(data: PlayerRegistration):
    active_players[data.player_id] = {'name': data.player_name, 'room_id': data.room_id, 'last_seen': time.time()}
    if data.room_id not in voice_message_queue: voice_message_queue[data.room_id] = []
    return {"status": "success", "room": data.room_id}

@app.post("/api/send_audio")
async def send_audio_message(message: VoiceMessage, background_tasks: BackgroundTasks):
    if message.sender_id not in active_players: raise HTTPException(status_code=400, detail="Отправитель не зарегистрирован")
    active_players[message.sender_id]['last_seen'] = time.time()
    msg_data = {'sender_id': message.sender_id, 'audio_data': message.audio_data, 'sequence': message.sequence, 'timestamp': time.time()}
    voice_message_queue[message.room_id].append(msg_data)
    background_tasks.add_task(cleanup_old_players)
    return {"status": "success"}

@app.get("/api/get_audio/{room_id}/{player_id}")
async def get_audio_messages(room_id: str, player_id: str):
    messages = voice_message_queue.get(room_id, [])
    filtered_messages = [msg for msg in messages if msg['sender_id'] != player_id]
    return {"status": "success", "messages": filtered_messages[-10:]}
