# main.py
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional
import uuid
import time
import asyncio
import os
import httpx  # Добавили для асинхронных запросов к OpenAI

app = FastAPI(title="Roblox Voice Chat & Moderation Server")

# Разрешаем запросы из Roblox Studio и игр
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажите конкретные домены!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Читаем ключ OpenAI из переменных окружения Render
OPENAI_API_KEY = os.getenv("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", "")

# --- Модели данных (Схемы) ---
class VoiceMessage(BaseModel):
    """Модель для получения голосового сообщения."""
    sender_id: str  # Уникальный ID игрока (например, UserId)
    room_id: str    # ID комнаты или сервера игры
    audio_data: str # Закодированные аудиоданные (например, в base64)
    sequence: int   # Порядковый номер пакета

class PlayerRegistration(BaseModel):
    """Модель для регистрации игрока в комнате."""
    player_id: str
    player_name: str
    room_id: str

class ModerationRequest(BaseModel):
    """Модель для модерации текста."""
    text: str

# --- Хранилище данных в памяти (Временное) ---
active_players: Dict[str, Dict] = {}  # player_id -> {name, room_id, last_seen}
voice_message_queue: Dict[str, List[Dict]] = {}  # room_id -> [messages]

# --- Вспомогательные функции ---
def cleanup_old_players():
    """Очистка игроков, которые не были активны более 30 секунд."""
    current_time = time.time()
    players_to_remove = []
    
    for player_id, data in active_players.items():
        if current_time - data.get('last_seen', 0) > 30:
            players_to_remove.append(player_id)
    
    for player_id in players_to_remove:
        room = active_players[player_id].get('room_id')
        active_players.pop(player_id, None)
        print(f"[CLEANUP] Удалён неактивный игрок: {player_id} из комнаты {room}")

# --- Основные эндпоинты API ---

@app.get("/")
async def root():
    return {"message": "Voice Chat & Moderation API работает", "status": "online"}

# ==========================================
# НОВЫЙ ЭНДПОИНТ: ПРОКСИ ДЛЯ МОДЕРАЦИИ OPENAI
# ==========================================
@app.post("/api/moderate")
async def moderate_text(request: ModerationRequest):
    """
    Прокси-метод для проверки текста через OpenAI Moderation API.
    """
    if not OPENAI_API_KEY:
        print("[MODERATION ERROR] API ключ OpenAI не задан в переменных окружения!")
        raise HTTPException(status_code=500, detail="На сервере не настроен API ключ OpenAI")

    url = "https://api.openai.com/v1/moderations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }
    data = {
        "input": request.text
    }

    try:
        # Делаем асинхронный запрос к серверам OpenAI
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=data, headers=headers, timeout=10.0)
            
            if response.status_code != 200:
                print(f"[MODERATION ERROR] OpenAI вернул код {response.status_code}: {response.text}")
                raise HTTPException(status_code=response.status_code, detail="Ошибка при запросе к OpenAI")
            
            result = response.json()
            
            # Извлекаем результат проверки
            if "results" in result and len(result["results"]) > 0:
                is_flagged = result["results"][0]["flagged"]
                return {
                    "status": "success",
                    "flagged": is_flagged,  # true если текст нарушает правила
                    "categories": result["results"][0]["categories"]
                }
            
            raise HTTPException(status_code=500, detail="Неверный формат ответа от OpenAI")

    except httpx.RequestError as exc:
        print(f"[MODERATION ERROR] Ошибка сети при запросе к OpenAI: {exc}")
        raise HTTPException(status_code=500, detail="Ошибка соединения с сервером OpenAI")

# ==========================================
# СОХРАНЁННЫЕ ЭНДПОИНТЫ ГОЛОСОВОГО ЧАТА
# ==========================================

@app.post("/api/register")
async def register_player(data: PlayerRegistration):
    player_id = data.player_id
    active_players[player_id] = {
        'name': data.player_name,
        'room_id': data.room_id,
        'last_seen': time.time()
    }
    if data.room_id not in voice_message_queue:
        voice_message_queue[data.room_id] = []
    print(f"[REGISTER] Игрок {data.player_name} ({player_id}) присоединился к комнате {data.room_id}")
    return {"status": "success", "room": data.room_id}

@app.post("/api/send_audio")
async def send_audio_message(message: VoiceMessage, background_tasks: BackgroundTasks):
    if message.sender_id not in active_players:
        raise HTTPException(status_code=400, detail="Отправитель не зарегистрирован")
    
    active_players[message.sender_id]['last_seen'] = time.time()
    room_id = message.room_id
    message_id = str(uuid.uuid4())[:8]
    message_data = {
        'id': message_id,
        'sender_id': message.sender_id,
        'sender_name': active_players[message.sender_id].get('name', 'Unknown'),
        'audio_data': message.audio_data,
        'sequence': message.sequence,
        'timestamp': time.time()
    }
    
    if room_id not in voice_message_queue:
        voice_message_queue[room_id] = []
    
    voice_message_queue[room_id].append(message_data)
    
    if len(voice_message_queue[room_id]) > 50:
        voice_message_queue[room_id] = voice_message_queue[room_id][-50:]
    
    background_tasks.add_task(cleanup_old_players)
    print(f"[AUDIO] Получено сообщение {message_id} от {message.sender_id} в комнате {room_id}")
    return {"status": "success", "message_id": message_id}

@app.get("/api/get_audio/{room_id}/{player_id}")
async def get_audio_messages(room_id: str, player_id: str):
    if player_id not in active_players:
        raise HTTPException(status_code=400, detail="Игрок не зарегистрирован")
    
    active_players[player_id]['last_seen'] = time.time()
    messages = voice_message_queue.get(room_id, [])
    filtered_messages = [msg for msg in messages if msg['sender_id'] != player_id]
    
    return {
        "status": "success",
        "room": room_id,
        "messages": filtered_messages[-10:],
        "player_count": len([p for p in active_players.values() if p.get('room_id') == room_id])
    }

@app.get("/api/players/{room_id}")
async def get_room_players(room_id: str):
    cleanup_old_players()
    room_players = []
    for player_id, data in active_players.items():
        if data.get('room_id') == room_id:
            room_players.append({
                'id': player_id,
                'name': data.get('name', 'Unknown'),
                'last_seen': data.get('last_seen', 0)
            })
    return {"room": room_id, "players": room_players, "count": len(room_players)}

if __name__ == "__main__":
    import uvicorn
    print("Запуск сервера голосового чата и модерации...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
