# server.py
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional
import uuid
import time
import asyncio

app = FastAPI(title="Roblox Voice Chat Server")

# Разрешаем запросы из Roblox Studio и игр
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажите конкретные домены!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# --- Хранилище данных в памяти (Временное) ---
# В реальном проекте здесь будет база данных (Redis, PostgreSQL)
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
    return {"message": "Voice Chat API работает", "status": "online"}

@app.post("/api/register")
async def register_player(data: PlayerRegistration):
    """
    Регистрация игрока в голосовом чате комнаты.
    Клиент должен вызывать этот метод при подключении.
    """
    player_id = data.player_id
    
    active_players[player_id] = {
        'name': data.player_name,
        'room_id': data.room_id,
        'last_seen': time.time()
    }
    
    # Инициализируем очередь сообщений для комнаты, если её нет
    if data.room_id not in voice_message_queue:
        voice_message_queue[data.room_id] = []
    
    print(f"[REGISTER] Игрок {data.player_name} ({player_id}) присоединился к комнате {data.room_id}")
    return {"status": "success", "room": data.room_id}

@app.post("/api/send_audio")
async def send_audio_message(message: VoiceMessage, background_tasks: BackgroundTasks):
    """
    Приём голосового сообщения от одного игрока и рассылка его другим.
    """
    # Проверяем, зарегистрирован ли отправитель
    if message.sender_id not in active_players:
        raise HTTPException(status_code=400, detail="Отправитель не зарегистрирован")
    
    # Обновляем время последней активности
    active_players[message.sender_id]['last_seen'] = time.time()
    
    room_id = message.room_id
    
    # Создаём объект сообщения для хранения
    message_id = str(uuid.uuid4())[:8]
    message_data = {
        'id': message_id,
        'sender_id': message.sender_id,
        'sender_name': active_players[message.sender_id].get('name', 'Unknown'),
        'audio_data': message.audio_data,
        'sequence': message.sequence,
        'timestamp': time.time()
    }
    
    # Добавляем сообщение в очередь комнаты (ограничиваем размер очереди)
    if room_id not in voice_message_queue:
        voice_message_queue[room_id] = []
    
    voice_message_queue[room_id].append(message_data)
    
    # Держим только последние 50 сообщений в очереди комнаты
    if len(voice_message_queue[room_id]) > 50:
        voice_message_queue[room_id] = voice_message_queue[room_id][-50:]
    
    # Запускаем фоновую задачу для очистки неактивных игроков
    background_tasks.add_task(cleanup_old_players)
    
    print(f"[AUDIO] Получено сообщение {message_id} от {message.sender_id} в комнате {room_id}")
    return {"status": "success", "message_id": message_id}

@app.get("/api/get_audio/{room_id}/{player_id}")
async def get_audio_messages(room_id: str, player_id: str):
    """
    Получение новых голосовых сообщений для игрока.
    Клиент должен периодически опрашивать этот эндпоинт.
    """
    # Проверяем регистрацию и обновляем активность
    if player_id not in active_players:
        raise HTTPException(status_code=400, detail="Игрок не зарегистрирован")
    
    active_players[player_id]['last_seen'] = time.time()
    
    # Возвращаем все сообщения из комнаты, кроме отправленных самим игроком
    messages = voice_message_queue.get(room_id, [])
    # Фильтруем сообщения от самого себя (чтобы не слышать свой же голос в ответ)
    filtered_messages = [msg for msg in messages if msg['sender_id'] != player_id]
    
    # Очищаем очередь после отправки (в реальной системе нужна более сложная логика)
    # voice_message_queue[room_id] = []
    
    return {
        "status": "success",
        "room": room_id,
        "messages": filtered_messages[-10:],  # Возвращаем только последние 10 сообщений
        "player_count": len([p for p in active_players.values() if p.get('room_id') == room_id])
    }

@app.get("/api/players/{room_id}")
async def get_room_players(room_id: str):
    """Получение списка активных игроков в комнате."""
    cleanup_old_players()  # Сначала чистим неактивных
    
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
    print("Запуск сервера голосового чата...")
    print("Документация API доступна по адресу: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
