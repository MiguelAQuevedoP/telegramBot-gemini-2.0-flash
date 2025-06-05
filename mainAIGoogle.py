#version make in 16/05/2025
#author: Miguel A. Quevedo P. & Claude AI (XD)
#email: mquevedo@unicauca.edu.co

import os
import logging
import sqlite3
import json
import requests
import pytz
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ConversationHandler, JobQueue

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuración de variables de entorno para Google AI Studio
API_KEY = 'API_KEY'  #API de Google AI Studio
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=API_KEY'

# Configuración para SheetDB
SHEETDB_API_URL = 'https://sheetdb.io/api/v1/API-KEY'  # API-KEY es la clave de SheetDB

# Estados para el ConversationHandler
DEVICE_ID = 1
AI_CONSULTATION = 2
REMINDER_MESSAGE = 3
REMINDER_TIME = 4

# Configuración del ConversationHandler corregida
def setup_conversation_handler():
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start), 
            CommandHandler("device", device_command),
            CallbackQueryHandler(handle_menu, pattern='^menu_ai$'),
            CallbackQueryHandler(handle_reminder_menu, pattern='^reminder_set$'),
            CallbackQueryHandler(cancel_planting_handler, pattern='^cancel_planting$'),  # Entry point para cancelación
            CallbackQueryHandler(handle_help_actions, pattern='^help_')  # Entry point para acciones de ayuda
        ],
        states={
            DEVICE_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_device_id_handler)
            ],
            AI_CONSULTATION: [
                MessageHandler(filters.TEXT | filters.PHOTO, handle_ai_consultation),
                CallbackQueryHandler(handle_menu, pattern='^menu_main$')
            ],
            REMINDER_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_message)
            ],
            REMINDER_TIME: [
                CallbackQueryHandler(handle_reminder_time, pattern='^(time_|menu_main)')
            ]
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(handle_menu, pattern='^menu_main$'),
            CallbackQueryHandler(cancel_planting_handler, pattern='^cancel_planting$'),  # También como fallback
            CallbackQueryHandler(handle_help_actions, pattern='^help_')  # También como fallback
        ],
        allow_reentry=True
    )
    return conv_handler

# Función para registrar selección de planta en SheetDB
def registrar_seleccion_planta(user_id, username, first_name, planta, device_id):
    try:
        # Preparar los datos para SheetDB
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Asegurarse de que los valores son strings para evitar errores
        user_id_str = str(user_id)
        username = username if username else "Sin username"
        first_name = first_name if first_name else "Sin nombre"
        
        data = {
            "Fecha": now,
            "UserID": user_id_str,
            "Username": username,
            "Nombre": first_name,
            "Planta": planta,
            "DispositivoID": device_id,
            "Plantado": "true"  # Asignar true cuando selecciona una planta
        }
        
        # Hacer la solicitud POST a SheetDB
        headers = {'Content-Type': 'application/json'}
        response = requests.post(
            SHEETDB_API_URL,
            json=data,
            headers=headers
        )
        
        # Verificar si la solicitud fue exitosa
        if response.status_code == 201 or response.status_code == 200:
            logger.info(f"Selección de planta registrada en SheetDB: {planta} por {username}")
            return True
        else:
            logger.error(f"Error al registrar en SheetDB. Código: {response.status_code}, Respuesta: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error al registrar selección en SheetDB: {e}")
        return False

# Función para consultar si el usuario tiene una planta activa
def consultar_estado_plantacion(user_id, device_id):
    try:
        # Construir URL para buscar por UserID y DispositivoID
        busqueda_url = f"{SHEETDB_API_URL}/search?UserID={user_id}&DispositivoID={device_id}"
        
        # Realizar la solicitud GET
        respuesta = requests.get(busqueda_url)
        
        if respuesta.status_code == 200:
            resultados = respuesta.json()
            
            # Verificar si se encontraron resultados y si tiene una planta activa
            if resultados and len(resultados) > 0:
                for fila in resultados:
                    if fila.get("Plantado", "").lower() == "true":
                        return True, fila.get("Planta", "desconocida")
            
            # Si no se encontró ninguna planta activa
            return False, ""
        else:
            logger.error(f"Error al consultar estado. Código: {respuesta.status_code}")
            return False, ""
            
    except Exception as e:
        logger.error(f"Error al consultar estado de plantación: {e}")
        return False, ""

# Configuración de base de datos
def init_db():
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    
    # Verificar si la tabla users existe
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    table_exists = cursor.fetchone()
    
    if not table_exists:
        # Crear tabla users si no existe
        cursor.execute('''
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            language TEXT DEFAULT 'es',
            last_activity TIMESTAMP,
            context TEXT,
            device_id TEXT
        )
        ''')
    else:
        # Verificar si la columna device_id existe
        cursor.execute("PRAGMA table_info(users)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        # Si device_id no existe, añadirla
        if 'device_id' not in column_names:
            cursor.execute("ALTER TABLE users ADD COLUMN device_id TEXT")
            logger.info("Columna device_id añadida a la tabla users")
    
    # Crear otras tablas si no existen
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        response TEXT,
        timestamp TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS plant_selections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plant_type TEXT,
        timestamp TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Base de datos inicializada correctamente")

# Función para inicializar la tabla de recordatorios en la base de datos
def init_reminders_table():
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT NOT NULL,
        reminder_time TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT 1,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Tabla de recordatorios inicializada correctamente")

# Funciones para manejar recordatorios
def save_reminder(user_id, message, reminder_time):
    """Guarda un recordatorio en la base de datos"""
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO reminders (user_id, message, reminder_time) VALUES (?, ?, ?)",
        (user_id, message, reminder_time)
    )
    reminder_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return reminder_id

def get_user_reminders(user_id):
    """Obtiene todos los recordatorios activos de un usuario"""
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, message, reminder_time FROM reminders WHERE user_id = ? AND is_active = 1 ORDER BY reminder_time",
        (user_id,)
    )
    reminders = cursor.fetchall()
    conn.close()
    return reminders

def delete_reminder(reminder_id):
    """Elimina un recordatorio de la base de datos"""
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE reminders SET is_active = 0 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def get_pending_reminders():
    """Obtiene todos los recordatorios que deben ser enviados"""
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    now = datetime.now()
    cursor.execute(
        "SELECT id, user_id, message FROM reminders WHERE reminder_time <= ? AND is_active = 1",
        (now,)
    )
    reminders = cursor.fetchall()
    conn.close()
    return reminders

# Funciones para interactuar con la base de datos
def register_user(user_id, username, first_name):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, last_activity) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, datetime.now())
    )
    conn.commit()
    conn.close()

def update_user_activity(user_id):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_activity = ? WHERE user_id = ?",
        (datetime.now(), user_id)
    )
    conn.commit()
    conn.close()

def save_interaction(user_id, message, response):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO interactions (user_id, message, response, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, message, response, datetime.now())
    )
    conn.commit()
    conn.close()

def save_plant_selection(user_id, plant_type):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO plant_selections (user_id, plant_type, timestamp) VALUES (?, ?, ?)",
        (user_id, plant_type, datetime.now())
    )
    conn.commit()
    conn.close()

def get_user_context(user_id):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT context FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        try:
            context = json.loads(result[0])
            # Validar y limpiar el contexto
            valid_context = []
            for item in context:
                if isinstance(item, dict) and "parts" in item:
                    # Verificar que tenga partes válidas
                    valid_parts = []
                    for part in item["parts"]:
                        if isinstance(part, dict) and "text" in part and part["text"].strip():
                            valid_parts.append(part)
                    
                    if valid_parts:
                        item["parts"] = valid_parts
                        valid_context.append(item)
            
            return valid_context
        except json.JSONDecodeError:
            logger.error(f"Error decodificando contexto para usuario {user_id}")
            return []
    
    return []

# Función para dividir mensajes largos
def split_message(text, max_length=4000):
    """Divide un mensaje largo en múltiples partes manteniendo párrafos completos"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_part = ""
    
    # Dividir por párrafos primero
    paragraphs = text.split('\n\n')
    
    for paragraph in paragraphs:
        # Si un párrafo individual es muy largo, dividirlo por oraciones
        if len(paragraph) > max_length:
            sentences = paragraph.split('. ')
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                    
                # Agregar punto si no termina en puntuación
                if not sentence.endswith(('.', '!', '?', ':')):
                    sentence += '.'
                
                if len(current_part + sentence) > max_length:
                    if current_part:
                        parts.append(current_part.strip())
                        current_part = sentence
                    else:
                        # Si una sola oración es muy larga, truncarla
                        parts.append(sentence[:max_length-3] + "...")
                else:
                    current_part += sentence + " "
        else:
            # Verificar si el párrafo cabe en la parte actual
            if len(current_part + paragraph) > max_length:
                if current_part:
                    parts.append(current_part.strip())
                    current_part = paragraph + "\n\n"
                else:
                    parts.append(paragraph)
            else:
                current_part += paragraph + "\n\n"
    
    # Agregar la última parte si no está vacía
    if current_part.strip():
        parts.append(current_part.strip())
    
    return parts


def set_user_context(user_id, context):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET context = ? WHERE user_id = ?", (json.dumps(context), user_id))
    conn.commit()
    conn.close()

def save_device_id(user_id, device_id):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    if device_id is None:
        cursor.execute("UPDATE users SET device_id = NULL WHERE user_id = ?", (user_id,))
    else:
        cursor.execute("UPDATE users SET device_id = ? WHERE user_id = ?", (device_id, user_id))
    conn.commit()
    conn.close()

def get_device_id(user_id):
    conn = sqlite3.connect('hydroponic_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT device_id FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] else None

# Función para conectar con Google AI Studio
def get_ai_response(prompt, context=None, image_data=None):
    if not API_KEY:
        logger.error("No se encontró la clave API de Google. Configura GOOGLE_API_KEY en las variables de entorno.")
        return "Error: API key no configurada."

    # Crear el payload para la solicitud a Google AI Studio (Gemini API)
    parts = []
    
    # Añadir imagen si se proporciona
    if image_data:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": image_data
            }
        })
    
    # Añadir texto (SIEMPRE debe haber texto)
    parts.append({"text": prompt})
    
    # Construir el contenido base
    contents = []
    
    # Añadir contexto previo si existe (FORMATO CORREGIDO)
    if context and len(context) > 0:
        for message in context:
            # Validar que el mensaje tenga la estructura correcta
            if isinstance(message, dict) and "parts" in message:
                # Verificar que las partes no estén vacías
                valid_parts = []
                for part in message["parts"]:
                    if isinstance(part, dict) and "text" in part and part["text"].strip():
                        valid_parts.append(part)
                
                if valid_parts:  # Solo agregar si hay partes válidas
                    # Convertir el formato de contexto a formato Gemini correcto
                    role = "user" if message.get("role") == "user" else "model"
                    contents.append({
                        "role": role,
                        "parts": valid_parts
                    })
    
    # Añadir el mensaje actual
    contents.append({
        "role": "user",
        "parts": parts
    })
    
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.01,
            "topK": 40,
            "topP": 0.95,
            "maxOutputTokens": 1024,
        }
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(ENDPOINT, json=payload, headers=headers)
        
        # Log para debugging
        logger.info(f"Request payload: {json.dumps(payload, indent=2)}")
        logger.info(f"Response status: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"API Error: {response.status_code} - {response.text}")
            return f"Error de API: {response.status_code}. Por favor, inténtalo de nuevo."
        
        response.raise_for_status()
        
        result = response.json()
        # Extraer la respuesta del formato de Gemini
        if "candidates" in result and len(result["candidates"]) > 0:
            candidate = result["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                return candidate["content"]["parts"][0]["text"]
            else:
                logger.error(f"Respuesta inesperada de la API: {result}")
                return "Error: Respuesta malformada de la API."
        else:
            logger.error(f"No se encontraron candidatos en la respuesta: {result}")
            return "No se pudo generar una respuesta válida."
    except Exception as e:
        logger.error(f"Error al conectar con Google AI Studio: {e}")
        return "Lo siento, ha ocurrido un error al procesar tu solicitud. Por favor, inténtalo de nuevo más tarde."

# Función para detectar si una imagen contiene plantas usando IA
def is_plant_image(image_data):
    prompt = "Analyze this image and respond with only 'YES' if it contains plants, flowers, vegetables, herbs, or any botanical elements. Respond with only 'NO' if it doesn't contain plants. Be very strict - only respond YES if there are clearly visible plants in the image."
    
    try:
        response = get_ai_response(prompt, image_data=image_data)
        return response.strip().upper() == 'YES'
    except Exception as e:
        logger.error(f"Error al analizar imagen: {e}")
        return False

# Comandos del bot
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name)
    
    # Verificar si el usuario ya tiene un ID de dispositivo registrado
    device_id = get_device_id(user.id)
    
    if not device_id:
        # Si no tiene ID de dispositivo, solicitarlo
        await update.message.reply_text(
            f"¡Hola {user.first_name}! 👋 Bienvenido a tu asistente para hidroponía NFT.\n\n"
            "Para comenzar, necesito que me proporciones el ID de tu dispositivo hidropónico."
        )
        return DEVICE_ID
    else:
        # Si ya tiene ID de dispositivo, mostrar menú principal
        keyboard = [
            [InlineKeyboardButton("🌱 Cultivos", callback_data='menu_plants')],
            [InlineKeyboardButton("🤖 Consultar IA", callback_data='menu_ai')],
            [InlineKeyboardButton("💧 Recordatorios", callback_data='menu_reminders')],
            [InlineKeyboardButton("ℹ️ Ayuda", callback_data='menu_help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"¡Hola {user.first_name}! 👋 Soy tu asistente para hidroponía NFT. ¿En qué puedo ayudarte hoy?",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

async def request_device_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Por favor, ingresa el ID de tu dispositivo hidropónico:"
    )
    return DEVICE_ID

async def save_device_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    device_id = update.message.text.strip()

    # Guardar el ID de dispositivo en la base de datos
    save_device_id(user_id, device_id)

    # Verificar si estamos en modo cancelación
    if context.user_data.get('cancel_mode', False):
        # Limpiar el flag de cancelación
        context.user_data.pop('cancel_mode', None)
        
        # Mostrar mensaje de confirmación específico para cancelación
        await update.message.reply_text(
            f"✅ Nuevo ID de dispositivo guardado: {device_id}\n\n"
            "Ahora puedes seleccionar una nueva planta para cultivar."
        )
    else:
        # Código para usuarios nuevos (primera vez)
        await update.message.reply_text(
            f"✅ ID de dispositivo guardado: {device_id}\n\n"
            "¡Perfecto! Tu dispositivo ha sido registrado exitosamente."
        )

    # Mostrar menú principal en ambos casos
    keyboard = [
        [InlineKeyboardButton("🌱 Cultivos", callback_data='menu_plants')],
        [InlineKeyboardButton("🤖 Consultar IA", callback_data='menu_ai')],
        [InlineKeyboardButton("💧 Recordatorios", callback_data='menu_reminders')],
        [InlineKeyboardButton("ℹ️ Ayuda", callback_data='menu_help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "¿En qué puedo ayudarte ahora?",
        reply_markup=reply_markup
    )

    return ConversationHandler.END

# Nueva función para manejar el menú de ayuda
async def show_help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el menú de ayuda con botones interactivos"""
    keyboard = [
        [InlineKeyboardButton("🔄 Reiniciar bot", callback_data='help_start')],
        [InlineKeyboardButton("🗑️ Limpiar conversación", callback_data='help_clear')],
        [InlineKeyboardButton("📱 Cambiar dispositivo", callback_data='help_device')],
        [InlineKeyboardButton("↩️ Volver al menú", callback_data='menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    help_text = (
        "ℹ️ **AYUDA - Asistente Hidropónico NFT**\n\n"
        "**¿Qué puedo hacer por ti?**\n\n"
        "🌱 **Cultivos**: Selección y registro de plantas en tu sistema NFT\n\n"
        "🤖 **Consultar IA**: Obtén respuestas sobre plantas e hidroponía. "
        "¡Puedes enviar fotos de plantas para análisis!\n\n"
        "💧 **Recordatorios**: Programa alertas para revisar agua, "
        "nutrientes, pH y mantenimiento\n\n"
        "**Acciones rápidas:**"
    )
    
    return help_text, reply_markup

# Nueva función para manejar las acciones de ayuda
async def handle_help_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja las acciones del menú de ayuda"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'help_start':
        # Reiniciar el bot - equivalente a /start
        user = query.from_user
        register_user(user.id, user.username, user.first_name)
        
        keyboard = [
            [InlineKeyboardButton("🌱 Cultivos", callback_data='menu_plants')],
            [InlineKeyboardButton("🤖 Consultar IA", callback_data='menu_ai')],
            [InlineKeyboardButton("💧 Recordatorios", callback_data='menu_reminders')],
            [InlineKeyboardButton("ℹ️ Ayuda", callback_data='menu_help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🔄 **Bot reiniciado**\n\n¡Hola {user.first_name}! 👋 "
            "Soy tu asistente para hidroponía NFT. ¿En qué puedo ayudarte?",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    elif query.data == 'help_clear':
        # Limpiar contexto - equivalente a /clear
        user_id = query.from_user.id
        set_user_context(user_id, [])
        
        keyboard = [
            [InlineKeyboardButton("🌱 Cultivos", callback_data='menu_plants')],
            [InlineKeyboardButton("🤖 Consultar IA", callback_data='menu_ai')],
            [InlineKeyboardButton("💧 Recordatorios", callback_data='menu_reminders')],
            [InlineKeyboardButton("ℹ️ Ayuda", callback_data='menu_help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🗑️ **Contexto limpiado**\n\n"
            "Se ha borrado el historial de conversación con la IA. "
            "¿En qué más puedo ayudarte?",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    elif query.data == 'help_device':
        # Cambiar dispositivo - equivalente a /device
        await query.edit_message_text(
            "📱 **Cambio de dispositivo**\n\n"
            "Por favor, ingresa el nuevo ID de tu dispositivo hidropónico:",
            parse_mode='Markdown'
        )
        return DEVICE_ID

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /help que muestra el menú de ayuda"""
    help_text, reply_markup = await show_help_menu(update, context)
    
    await update.message.reply_text(
        help_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def device_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await request_device_id(update, context)

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_context(user_id, [])
    await update.message.reply_text("Contexto de conversación borrado. ¿En qué más puedo ayudarte?")

async def start_ai_consultation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Crear teclado con opción de regresar al menú
    keyboard = [
        [InlineKeyboardButton("🏠 Regresar al menú principal", callback_data='menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text="🤖 Modo consulta IA activado\n\n"
             "Puedes enviarme:\n"
             "• Preguntas sobre plantas y hidroponía\n"
             "• Fotos de plantas para análisis\n\n"
             "💡 Solo acepto fotos que contengan plantas.",
        reply_markup=reply_markup
    )
    
    # Activar el modo consulta IA
    context.user_data['ai_mode'] = True
    return AI_CONSULTATION

async def handle_ai_consultation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Crear teclado para regresar al menú
    keyboard = [
        [InlineKeyboardButton("🏠 Regresar al menú principal", callback_data='menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Verificar si es una foto
    if update.message.photo:
        try:
            # Obtener la foto de mayor resolución
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            
            # Descargar la imagen
            photo_bytes = await file.download_as_bytearray()
            
            # Convertir a base64
            import base64
            image_data = base64.b64encode(photo_bytes).decode('utf-8')
            
            # Verificar si la imagen contiene plantas
            if not is_plant_image(image_data):
                await update.message.reply_text(
                    "❌ Lo siento, solo acepto fotos de plantas.\n"
                    "Por favor, envía una imagen que contenga plantas para que pueda ayudarte con información sobre ellas.",
                    reply_markup=reply_markup
                )
                return AI_CONSULTATION
            
            # Procesar la imagen con IA - Prompt más conciso
            prompt = "Analiza brevemente esta imagen de plantas (máximo 500 palabras). Incluye: estado de la planta, problemas visibles, y cuidados para hidroponía NFT."
            
            # Obtener contexto del usuario
            user_context = get_user_context(user_id)
            
            # Obtener respuesta de la IA
            response = get_ai_response(prompt, user_context, image_data)
            
            # Verificar si la respuesta no es un error
            if not response.startswith("Error") and not response.startswith("Lo siento"):
                # Actualizar contexto (FORMATO CORREGIDO)
                user_context.append({
                    "role": "user", 
                    "parts": [{"text": "Imagen de planta enviada"}]
                })
                user_context.append({
                    "role": "model", 
                    "parts": [{"text": response[:500]}]  # Truncar para contexto
                })
                
                # Mantener contexto limitado
                if len(user_context) > 6:  # Reducido aún más
                    user_context = user_context[-6:]
                
                set_user_context(user_id, user_context)
                
                # Guardar interacción
                save_interaction(user_id, "Imagen de planta", response[:1000])  # Truncar para BD
            
            # Dividir respuesta si es necesario
            message_parts = split_message(response)
            
            # Enviar cada parte
            for i, part in enumerate(message_parts):
                if i == len(message_parts) - 1:  # Último mensaje
                    await update.message.reply_text(part, reply_markup=reply_markup)
                else:
                    await update.message.reply_text(part)
            
        except Exception as e:
            logger.error(f"Error procesando imagen: {e}")
            await update.message.reply_text(
                "❌ Error al procesar la imagen. Por favor, inténtalo de nuevo.",
                reply_markup=reply_markup
            )
    
    # Si es texto
    elif update.message.text:
        message = update.message.text
        
        # Obtener contexto del usuario
        user_context = get_user_context(user_id)
        
        # Añadir contexto especializado en plantas - Prompt más conciso
        specialized_prompt = f"Como experto en hidroponía, responde brevemente (máximo 400 palabras): {message}"
        
        # Obtener respuesta de la IA
        response = get_ai_response(specialized_prompt, user_context)
        
        # Verificar si la respuesta no es un error
        if not response.startswith("Error") and not response.startswith("Lo siento"):
            # Actualizar contexto (FORMATO CORREGIDO)
            user_context.append({
                "role": "user", 
                "parts": [{"text": message}]  # Usar mensaje original, no el prompt especializado
            })
            user_context.append({
                "role": "model", 
                "parts": [{"text": response[:500]}]  # Truncar para contexto
            })
            
            # Mantener contexto limitado
            if len(user_context) > 6:  # Reducido aún más
                user_context = user_context[-6:]
            
            set_user_context(user_id, user_context)
            
            # Guardar interacción
            save_interaction(user_id, message, response[:1000])  # Truncar para BD
        
        # Dividir respuesta si es necesario
        message_parts = split_message(response)
        
        # Enviar cada parte
        for i, part in enumerate(message_parts):
            if i == len(message_parts) - 1:  # Último mensaje
                await update.message.reply_text(part, reply_markup=reply_markup)
            else:
                await update.message.reply_text(part)
    
    return AI_CONSULTATION

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'menu_main':
        # Desactivar modo IA si estaba activo
        context.user_data['ai_mode'] = False
        
        keyboard = [
            [InlineKeyboardButton("🌱 Cultivos", callback_data='menu_plants')],
            [InlineKeyboardButton("🤖 Consultar IA", callback_data='menu_ai')],
            [InlineKeyboardButton("💧 Recordatorios", callback_data='menu_reminders')],
            [InlineKeyboardButton("ℹ️ Ayuda", callback_data='menu_help')]  # ← Agregar esta línea
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="Menú principal - ¿Qué deseas hacer?",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    elif query.data == 'menu_ai':
        return await start_ai_consultation(update, context)
    
    elif query.data == 'menu_plants':
        user_id = query.from_user.id
        device_id = get_device_id(user_id)
        
        # Consultar si el usuario tiene una planta activa
        tiene_planta_activa, planta_actual = consultar_estado_plantacion(user_id, device_id)
        
        if tiene_planta_activa:
            # El usuario ya tiene una planta activa, mostrar mensaje y opciones
            keyboard = [
                [InlineKeyboardButton("❌ Cancelar plantación actual", callback_data='cancel_planting')],
                [InlineKeyboardButton("↩️ Volver al menú", callback_data='menu_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text=f"🌱 Actualmente tienes una plantación activa de {planta_actual}.\n\n"
                     "Para seleccionar una nueva planta, primero debes cancelar la plantación actual.",
                reply_markup=reply_markup
            )
        else:
            # El usuario no tiene planta activa, mostrar opciones de plantas
            keyboard = [
                [InlineKeyboardButton("🥬 Lechuga", callback_data='plant_lechuga')],
                [InlineKeyboardButton("🌿 Acelga", callback_data='plant_acelga')],
                [InlineKeyboardButton("🍃 Espinaca", callback_data='plant_espinaca')],
                [InlineKeyboardButton("🌿 Aromáticas", callback_data='plant_aromaticas')],
                [InlineKeyboardButton("🌶️ Chile/Pimiento", callback_data='plant_chile')],
                [InlineKeyboardButton("🍅 Jitomate", callback_data='plant_jitomate')],
                [InlineKeyboardButton("🌸 Ornamentales", callback_data='plant_ornamentales')],
                [InlineKeyboardButton("↩️ Volver", callback_data='menu_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text="Selecciona un cultivo para registrar en tu sistema:",
                reply_markup=reply_markup
            )
    
    elif query.data == 'cancel_planting':
        return await cancel_planting_handler(update, context)

    elif query.data == 'menu_reminders':
        keyboard = [
            [InlineKeyboardButton("⏰ Configurar recordatorio", callback_data='reminder_set')],
            [InlineKeyboardButton("📝 Ver recordatorios", callback_data='reminder_list')],
            [InlineKeyboardButton("↩️ Volver", callback_data='menu_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="🔔 **Gestión de Recordatorios**\n\n"
                 "Configura recordatorios para:\n"
                 "• Revisar nivel de agua\n"
                 "• Cambiar nutrientes\n" 
                 "• Verificar pH\n"
                 "• Limpiar sistema\n"
                 "• Cualquier tarea de mantenimiento",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    elif query.data == 'menu_help':
        help_text, reply_markup = await show_help_menu(update, context)
        await query.edit_message_text(
            text=help_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    elif query.data.startswith('plant_'):
        plant_type = query.data.split('_')[1]
        user_id = query.from_user.id
        user = query.from_user
        device_id = get_device_id(user_id)
        
        # Guardar selección en la base de datos local
        save_plant_selection(user_id, plant_type)
        
        # Registrar selección en SheetDB con ID de dispositivo
        registrado_sheets = registrar_seleccion_planta(
            user_id, 
            user.username if user.username else "Sin username",
            user.first_name if user.first_name else "Sin nombre",
            plant_type,
            device_id
        )
        
        # Mensaje de confirmación simple
        if registrado_sheets:
            response = f"✅ {plant_type.capitalize()} registrada exitosamente en tu sistema hidropónico.\n\n"
            response += "Tu plantación está ahora activa y registrada en nuestra base de datos."
        else:
            response = f"❌ Error al registrar {plant_type}. Por favor, inténtalo de nuevo."
        
        await query.edit_message_text(
            text=response,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Volver", callback_data='menu_plants')]])
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Verificar si estamos en modo consulta IA
    if context.user_data.get('ai_mode', False):
        return await handle_ai_consultation(update, context)
    
    # Verificar si el usuario ya tiene un ID de dispositivo
    device_id = get_device_id(user_id)
    
    if not device_id:
        # Si no tiene ID, solicitarlo
        return await request_device_id(update, context)
    
    # Si no está en modo IA, redirigir al menú principal
    keyboard = [
        [InlineKeyboardButton("🌱 Cultivos", callback_data='menu_plants')],
        [InlineKeyboardButton("🤖 Consultar IA", callback_data='menu_ai')],
        [InlineKeyboardButton("💧 Recordatorios", callback_data='menu_reminders')],
        [InlineKeyboardButton("ℹ️ Ayuda", callback_data='menu_help')]  # ← Agregar esta línea
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Para interactuar conmigo, por favor usa el menú de opciones:",
        reply_markup=reply_markup
    )

#Maneja específicamente la cancelación de plantación y establece un flag cancel_mode en el contexto del usuario.
async def cancel_planting_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la cancelación de plantación y solicita nuevo ID de dispositivo"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    device_id = get_device_id(user_id)

    # Eliminar el registro del usuario en SheetDB
    try:
        # Construir URL para eliminar por UserID y DispositivoID
        delete_url = f"{SHEETDB_API_URL}/UserID/{user_id}/DispositivoID/{device_id}"
        response = requests.delete(delete_url)
        
        if response.status_code in [200, 204]:
            logger.info(f"Registro eliminado de SheetDB para user_id {user_id} y device_id {device_id}")
        else:
            logger.warning(f"Respuesta inesperada al eliminar de SheetDB: {response.status_code}")
    except Exception as e:
        logger.error(f"Error al eliminar registro de SheetDB: {e}")

    # Limpiar el device_id en la base local
    save_device_id(user_id, None)

    await query.edit_message_text(
        text="❌ Plantación cancelada y datos eliminados.\n\n"
             "🔁 Por favor, ingresa nuevamente el ID de tu dispositivo hidropónico:"
    )
    
    # Marcar que estamos en modo cancelación para manejar diferente el siguiente input
    context.user_data['cancel_mode'] = True
    return DEVICE_ID # Retornar el estado para capturar el nuevo device_id

# Función para enviar recordatorios
async def send_reminders_job(context: ContextTypes.DEFAULT_TYPE):
    """Job que se ejecuta cada minuto para verificar recordatorios pendientes - CORREGIDO"""
    try:
        pending_reminders = get_pending_reminders()
        
        for reminder_id, user_id, message in pending_reminders:
            try:
                # Enviar el recordatorio al usuario
                keyboard = [
                    [InlineKeyboardButton("🏠 Menú principal", callback_data='menu_main')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏰ **RECORDATORIO**\n\n{message}",
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                
                # Eliminar el recordatorio después de enviarlo
                delete_reminder(reminder_id)
                logger.info(f"Recordatorio {reminder_id} enviado y eliminado para usuario {user_id}")
                
            except Exception as e:
                logger.error(f"Error enviando recordatorio {reminder_id} a usuario {user_id}: {e}")
                # Eliminar recordatorio fallido para evitar spam
                delete_reminder(reminder_id)
                
    except Exception as e:
        logger.error(f"Error en job de recordatorios: {e}")

# Manejadores para configurar recordatorios
async def handle_reminder_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'reminder_set':
        await query.edit_message_text(
            text="⏰ **Configurar Recordatorio**\n\n"
                 "Escribe el mensaje que quieres recordar.\n"
                 "Ejemplo: 'Revisar nivel de agua' o 'Cambiar nutrientes'",
            parse_mode='Markdown'
        )
        return REMINDER_MESSAGE
        
    elif query.data == 'reminder_list':
        user_id = query.from_user.id
        reminders = get_user_reminders(user_id)
        
        if not reminders:
            keyboard = [
                [InlineKeyboardButton("➕ Crear recordatorio", callback_data='reminder_set')],
                [InlineKeyboardButton("↩️ Volver", callback_data='menu_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text="📝 No tienes recordatorios activos.",
                reply_markup=reply_markup
            )
        else:
            # Mostrar lista de recordatorios
            colombia_tz = pytz.timezone('America/Bogota')
            text = "📝 **Tus Recordatorios Activos:**\n\n"
            keyboard = []
            
            for i, (reminder_id, message, reminder_time) in enumerate(reminders[:5], 1):  # Máximo 5 recordatorios
                try:
                    # Convertir la fecha a timezone de Colombia - CORREGIDO
                    if isinstance(reminder_time, str):
                        # Intentar diferentes formatos de fecha
                        try:
                            # Formato con microsegundos
                            dt = datetime.strptime(reminder_time, '%Y-%m-%d %H:%M:%S.%f')
                        except ValueError:
                            try:
                                # Formato sin microsegundos
                                dt = datetime.strptime(reminder_time, '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                # Formato ISO con T
                                dt = datetime.fromisoformat(reminder_time.replace('T', ' ').replace('Z', ''))
                    else:
                        dt = reminder_time
                    
                    # Asegurar que la fecha tenga timezone UTC antes de convertir
                    if dt.tzinfo is None:
                        dt = pytz.utc.localize(dt)
                    
                    dt_colombia = dt.astimezone(colombia_tz)
                    fecha_str = dt_colombia.strftime('%d/%m/%Y %I:%M %p')
                    
                    text += f"{i}. {message}\n📅 {fecha_str}\n\n"
                    
                    # Botón para cancelar este recordatorio
                    keyboard.append([InlineKeyboardButton(
                        f"❌ Cancelar recordatorio {i}", 
                        callback_data=f'cancel_reminder_{reminder_id}'
                    )])
                    
                except Exception as e:
                    logger.error(f"Error procesando recordatorio {reminder_id}: {e}")
                    # Mostrar recordatorio con fecha sin procesar
                    text += f"{i}. {message}\n📅 {reminder_time}\n\n"
                    keyboard.append([InlineKeyboardButton(
                        f"❌ Cancelar recordatorio {i}", 
                        callback_data=f'cancel_reminder_{reminder_id}'
                    )])
            
            # Botones de navegación
            keyboard.extend([
                [InlineKeyboardButton("➕ Nuevo recordatorio", callback_data='reminder_set')],
                [InlineKeyboardButton("↩️ Volver", callback_data='menu_main')]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text=text, parse_mode='Markdown', reply_markup=reply_markup)
    
    elif query.data.startswith('cancel_reminder_'):
        reminder_id = int(query.data.split('_')[2])
        delete_reminder(reminder_id)
        
        await query.edit_message_text(
            text="✅ Recordatorio cancelado exitosamente.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📝 Ver recordatorios", callback_data='reminder_list'),
                InlineKeyboardButton("↩️ Menú", callback_data='menu_main')
            ]])
        )

# Función auxiliar para manejar la conversión de fechas de manera más robusta
def parse_datetime_flexible(date_string):
    """
    Convierte una cadena de fecha a datetime manejando diferentes formatos
    """
    if isinstance(date_string, datetime):
        return date_string
    
    formats_to_try = [
        '%Y-%m-%d %H:%M:%S.%f',  # Con microsegundos
        '%Y-%m-%d %H:%M:%S',     # Sin microsegundos
        '%Y-%m-%dT%H:%M:%S.%f',  # ISO con T y microsegundos
        '%Y-%m-%dT%H:%M:%S',     # ISO con T sin microsegundos
        '%Y-%m-%dT%H:%M:%S.%fZ', # ISO con Z
        '%Y-%m-%dT%H:%M:%SZ'     # ISO con Z sin microsegundos
    ]
    
    for fmt in formats_to_try:
        try:
            return datetime.strptime(date_string, fmt)
        except ValueError:
            continue
    
    # Si ningún formato funciona, usar fromisoformat como último recurso
    try:
        return datetime.fromisoformat(date_string.replace('T', ' ').replace('Z', ''))
    except ValueError:
        raise ValueError(f"No se pudo parsear la fecha: {date_string}")


async def handle_reminder_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captura el mensaje del recordatorio"""
    message = update.message.text.strip()
    
    if len(message) > 200:
        await update.message.reply_text(
            "❌ El mensaje es muy largo. Por favor, usa máximo 200 caracteres."
        )
        return REMINDER_MESSAGE
    
    # Guardar el mensaje en el contexto del usuario
    context.user_data['reminder_message'] = message
    
    # Crear teclado con opciones de tiempo predefinidas
    keyboard = [
        [InlineKeyboardButton("⏰ 15 minutos", callback_data='time_15m')],
        [InlineKeyboardButton("⏰ 30 minutos", callback_data='time_30m')],
        [InlineKeyboardButton("⏰ 1 hora", callback_data='time_1h')],
        [InlineKeyboardButton("⏰ 2 horas", callback_data='time_2h')],
        [InlineKeyboardButton("⏰ 6 horas", callback_data='time_6h')],
        [InlineKeyboardButton("⏰ 12 horas", callback_data='time_12h')],
        [InlineKeyboardButton("⏰ 1 día", callback_data='time_1d')],
        [InlineKeyboardButton("⏰ 3 días", callback_data='time_3d')],
        [InlineKeyboardButton("❌ Cancelar", callback_data='menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Mensaje guardado: *{message}*\n\n"
        "🕐 ¿Cuándo quieres recibir este recordatorio?",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    return REMINDER_TIME

async def handle_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa la selección de tiempo y guarda el recordatorio"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'menu_main':
        return await handle_menu(update, context)
    
    # Obtener el mensaje guardado
    reminder_message = context.user_data.get('reminder_message')
    if not reminder_message:
        await query.edit_message_text("❌ Error: No se encontró el mensaje del recordatorio.")
        return ConversationHandler.END
    
    # Calcular el tiempo del recordatorio
    time_mapping = {
        'time_15m': 15,           # 15 minutos
        'time_30m': 30,           # 30 minutos  
        'time_1h': 60,            # 1 hora
        'time_2h': 120,           # 2 horas
        'time_6h': 360,           # 6 horas
        'time_12h': 720,          # 12 horas
        'time_1d': 1440,          # 1 día
        'time_3d': 4320           # 3 días
    }
    
    minutes = time_mapping.get(query.data)
    if not minutes:
        await query.edit_message_text("❌ Opción no válida.")
        return ConversationHandler.END
    
    # Calcular fecha y hora del recordatorio
    colombia_tz = pytz.timezone('America/Bogota')
    now = datetime.now(colombia_tz)
    reminder_time = now + timedelta(minutes=minutes)
    
    # Convertir a UTC para guardar en la base de datos
    reminder_time_utc = reminder_time.astimezone(pytz.utc).replace(tzinfo=None)
    
    # Guardar el recordatorio
    user_id = query.from_user.id
    reminder_id = save_reminder(user_id, reminder_message, reminder_time_utc)
    
    # Limpiar datos temporales
    context.user_data.pop('reminder_message', None)
    
    # Mostrar confirmación
    time_labels = {
        'time_15m': '15 minutos',
        'time_30m': '30 minutos',
        'time_1h': '1 hora',
        'time_2h': '2 horas',
        'time_6h': '6 horas',
        'time_12h': '12 horas',
        'time_1d': '1 día',
        'time_3d': '3 días'
    }
    
    time_label = time_labels.get(query.data, 'tiempo seleccionado')
    fecha_str = reminder_time.strftime('%d/%m/%Y %I:%M %p')
    
    keyboard = [
        [InlineKeyboardButton("📝 Ver recordatorios", callback_data='reminder_list')],
        [InlineKeyboardButton("➕ Otro recordatorio", callback_data='reminder_set')],
        [InlineKeyboardButton("🏠 Menú principal", callback_data='menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✅ **Recordatorio configurado**\n\n"
        f"📝 Mensaje: {reminder_message}\n"
        f"⏰ En: {time_label}\n"
        f"📅 Fecha: {fecha_str}",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    return ConversationHandler.END

def main():
    # Inicializar la base de datos
    init_db()
    init_reminders_table() 
    
    # Obtener el token de Telegram del ambiente
    token = 'TELEGRAM_BOT_TOKEN' # Reemplazar con token real TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("No se encontró el token de Telegram. Configura TELEGRAM_BOT_TOKEN en las variables de entorno.")
        return
    
    # Crear la aplicación
    application = Application.builder().token(token).build()

    # Configurar el job para verificar recordatorios cada minuto
    job_queue = application.job_queue
    job_queue.run_repeating(send_reminders_job, interval=60, first=10)
    
    # Usar la función setup_conversation_handler en lugar de crear aquí
    conv_handler = setup_conversation_handler()
    
    # Añadir manejadores
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_context))
    application.add_handler(CallbackQueryHandler(handle_reminder_menu, pattern='^(reminder_|cancel_reminder_)'))
    application.add_handler(CallbackQueryHandler(handle_menu))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Iniciar el bot
    application.run_polling()

if __name__ == "__main__":
    main()