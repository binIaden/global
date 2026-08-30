import asyncio
import os
import time
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ============================================================
# CONFIGURACIÓN (variables de entorno para Railway)
# ============================================================
API_ID = int(os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")

BOT = "@Globalccvs_Bot"

# ÚNICO bot que dispara el flujo de compra
TRIGGER_USERNAME = "ccscards_bot"   # sin @, minúsculas

# StringSession generada previamente (variable de entorno TELEGRAM_SESSION)
SESSION_STRING = os.environ.get("TELEGRAM_SESSION", "").strip()

# productos.txt: contenido subido como variable de entorno PRODUCTOS_CONTENT
PRODUCTOS_FILE = "productos.txt"
MAX_PRICE = float(os.environ.get("MAX_PRICE", 5.0))

# Timeout global para esperar respuestas del bot
TIMEOUT = 45

# Límite de seguridad anti-bucle infinito
MAX_PAGES = 300

# Crear productos.txt desde la variable de entorno si no existe
if not os.path.exists(PRODUCTOS_FILE):
    with open(PRODUCTOS_FILE, "w", encoding="utf-8") as f:
        f.write(os.environ.get("PRODUCTOS_CONTENT", ""))


client = TelegramClient(
    StringSession(SESSION_STRING) if SESSION_STRING else "telegram_session",
    API_ID,
    API_HASH,
    catch_up=True,
    sequential_updates=True
)

INSUFFICIENT_MSG = "Current user's account balance is insufficient. Please return to the homepage to recharge or adjust the amount."

used_buttons = set()

# ID numérico del bot (se resuelve al arrancar, evita bugs de resolución por username)
BOT_ID = None


# ============================================================
# ESPERA DE RESPUESTA (clic no bloqueante + fallback)
# ============================================================

class BotMessageWaiter:
    def __init__(self):
        self.future = None
        self.handler = None
        self.handler_edit = None
        self._t0 = 0

    def _register_handlers(self):
        async def on_new(event):
            # Filtro manual por ID (no depende de resolución de username)
            if BOT_ID is not None and event.sender_id != BOT_ID:
                return
            if event.message.out:  # ignorar nuestros propios mensajes
                return
            msg = event.message
            print(f"   [debug] NewMessage a los {time.perf_counter()-self._t0:.2f}s (id={msg.id})")
            if not self.future.done():
                self.future.set_result(msg)
                print("   [debug] >>> FUTURE RESUELTO <<<")

        async def on_edit(event):
            if BOT_ID is not None and event.sender_id != BOT_ID:
                return
            msg = await event.get_message()
            if msg.out:
 return
            print(f"   [debug] MessageEdited a los {time.perf_counter()-self._t0:.2f}s (id={msg.id})")
            if not self.future.done():
                self.future.set_result(msg)

        self.handler = on_new
        self.handler_edit = on_edit
        client.add_event_handler(on_new, events.NewMessage())
        client.add_event_handler(on_edit, events.MessageEdited())

    def _unregister_handlers(self):
        if self.handler:
            client.remove_event_handler(self.handler, events.NewMessage())
            self.handler = None
        if self.handler_edit:
            client.remove_event_handler(self.handler_edit, events.MessageEdited())
            self.handler_edit = None

    async def _poll_fallback(self):
        try:
            messages = await client.get_messages(BOT, limit=3)
            for m in messages:
                if not m.out:
                    # Solo aceptar mensajes recientes (menos de 60s de antigüedad)
                    age = time.time() - m.date.timestamp()
                    if age < 60:
                        return m
                    print(f"   [fallback] Mensaje id={m.id} descartado (antigüedad {age:.0f}s)")
        except Exception as e:
            print(f"   [fallback] Error: {e}")
        return None

    async def click_and_wait(self, message, text, timeout=TIMEOUT):
        self.future = asyncio.get_running_loop().create_future()
        self._t0 = time.perf_counter()
        self._register_handlers()

        click_task = asyncio.create_task(message.click(text=text))

        result = None
        try:
            result = await asyncio.wait_for(self.future, timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._unregister_handlers()

        if result is None:
            try:
                await asyncio.wait_for(click_task, timeout=3)
            except Exception:
                pass

        if result is None:
            print("   [fallback] Evento no recibido, consultando historial...")
            result = await self._poll_fallback()
            if result:
                print(f"   [fallback] Mensaje encontrado: id={result.id}")

        return result

    async def prepare(self):
        self.future = asyncio.get_running_loop().create_future()
        self._t0 = time.perf_counter()
        self._register_handlers()
        return self.future

    async def wait(self, timeout=TIMEOUT, fallback=True):
        result = None
        try:
            result = await asyncio.wait_for(self.future, timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._unregister_handlers()

        if result is None and fallback:
            print("   [fallback] Evento no recibido, consultando historial...")
            result = await self._poll_fallback()
            if result:
                print(f"   [fallback] Mensaje encontrado: id={result.id}")
        return result


# ============================================================
# UTILIDADES DE DEBUG
# ============================================================

def _dump_buttons(message):
    if message.buttons:
        for r_i, row in enumerate(message.buttons):
            for b in row:
                print(f"   [{r_i}] {b.text!r}")
    else:
        print("   (mensaje sin botones) Texto:", repr((message.text or "")[:120]))


# ============================================================
# PRODUCTOS
# ============================================================

def load_products():
    products = []
    with open(PRODUCTOS_FILE, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            product_id = line.strip()
            if not product_id:
                continue
            products.append({
                "id": product_id,
                "priority": line_number
            })
    return products


# ============================================================
# MENSAJES / ARTÍCULOS / BOTONES
# ============================================================

def print_message(message):
    print("\n" + "=" * 60)
    print("ID:", message.id)
    print("TEXTO:")
    print(message.text or "(sin texto)")
    if message.buttons:
        print("\nBOTONES:")
        for row_index, row in enumerate(message.buttons):
            for column_index, button in enumerate(row):
                print(f"[{row_index},{column_index}] {button.text}")


def get_items(message):
    items = []
    if not message.buttons:
        return items
    for row in message.buttons:
        for button in row:
            if "|" in button.text:
                items.append(button.text)
    return items


async def find_button(message, text):
    if not message.buttons:
        return None
    for row in message.buttons:
        for button in row:
            if button.text.strip().lower() == text.strip().lower():
                return button
    return None


async def find_check_button(message):
    if not message.buttons:
        return None
    for row in message.buttons:
        for button in row:
            if "check" in button.text.lower():
                return button
    return None


def extract_id(item_text):
    parts = item_text.split("|")
    if len(parts) < 2:
        return None
    return parts[0].strip()


def extract_price(item_text):
    parts = item_text.split("|")
    if len(parts) < 2:
        return None
    price_text = parts[1].strip()
    price_text = price_text.replace("💵", "").replace("$", "").replace("USD", "").strip()
    try:
        return float(price_text.replace(",", "."))
    except ValueError:
        return None


# ============================================================
# FILTRO DE ARTÍCULOS DE UNA SOLA PÁGINA
# ============================================================

def filter_page_items(items, products, page_num):
    """Filtra los artículos de UNA página y los ordena por prioridad."""
    product_ids = {p["id"]: p["priority"] for p in products}
    valid = []

    print(f"\n   [debug] Analizando {len(items)} artículos de la página {page_num}...")

    for item in items:
        item_id = extract_id(item)
        price = extract_price(item)

        if item_id is None or price is None:
            print(f"   [debug] Pág {page_num} | ilegible | ✗ RECHAZADO: {item!r}")
            continue
        if item_id not in product_ids:
            print(f"   [debug] Pág {page_num} | {item_id} | ${price} | ✗ NO está en productos.txt")
            continue
        if price > MAX_PRICE:
            print(f"   [debug] Pág {page_num} | {item_id} | price:.2f∣✗precio>{price:.2f} | ✗ precio >price:.2f∣✗precio>{MAX_PRICE}")
            continue

        print(f"   [debug] Pág {page_num} | {item_id} | ${price:.2f} | ✓ VÁLIDO")
        valid.append({
            "id": item_id,
            "item": item,
            "price": price,
            "priority": product_ids[item_id],
            "page": page_num
        })

    # Deduplicar por texto exacto; más barato primero dentro de la misma prioridad
    seen = set()
    unique_list = []
    for rec in sorted(valid, key=lambda x: x["price"]):
        if rec["item"] not in seen:
            seen.add(rec["item"])
            unique_list.append(rec)
    unique_list.sort(key=lambda x: (x["priority"], x["price"]))
    return unique_list


# ============================================================
# NAVEGACIÓN
# ============================================================

async def navigate_to_page(current_page, target_page, message):
    while current_page < target_page:
        next_btn = await find_button(message, "next page ➡️")
        if not next_btn:
            print("No se encontró botón next page")
            return None
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        new_msg = await waiter.click_and_wait(message, next_btn.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibió la página siguiente")
            return None
        message = new_msg
        current_page += 1

    while current_page > target_page:
        prev_btn = await find_button(message, "Previous")
        if not prev_btn:
            print("No se encontró botón Previous")
            return None
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        new_msg = await waiter.click_and_wait(message, prev_btn.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibió la página anterior")
            return None
        message = new_msg
        current_page -= 1

    return message


async def click_item_available(message, item_text, used_buttons):
    if not message.buttons:
        return False
    for row in message.buttons:
        for button in row:
            if button.text.strip() == item_text.strip():
                button_key = str(button.data) if button.data else None
                if button_key and button_key in used_buttons:
                    continue
                return True
    return False


async def purchase_item(record, current_page, message):
    print(f"\n>>> Comprando: {record['item']} (página {record['page']}, prioridad {record['priority']})")

    if current_page != record["page"]:
        print(f"Navegando de página {current_page} a {record['page']}...")
        message = await navigate_to_page(current_page, record["page"], message)
        if not message:
            return True, current_page, message
        current_page = record["page"]

    available = await click_item_available(message, record["item"], used_buttons)
    if not available:
        print("   ✗ No se pudo hacer clic en el artículo (posiblemente ya fue usado)")
        return True, current_page, message

    waiter = BotMessageWaiter()
    t0 = time.perf_counter()
    response = await waiter.click_and_wait(message, record["item"], timeout=TIMEOUT)
    print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
    if response is None:
        print("   ✗ No hubo respuesta")
        return True, current_page, message

    used_buttons.add(record["item"])

    if response.text and INSUFFICIENT_MSG in response.text:
        print("   ✗ Saldo insuficiente detectado. Deteniendo compras.")
        return False, current_page, message

    print("   Respuesta del bot:")
    print_message(response)

    check_btn = await find_check_button(response)
    if check_btn:
        print("   -> Botón check encontrado, haciendo clic...")
        waiter = BotMessageWaiter()
        final = await waiter.click_and_wait(response, check_btn.text, timeout=TIMEOUT)
        if final:
            final_text = final.text or ""
            if INSUFFICIENT_MSG in final_text:
                print("   ✗ Saldo insuficiente después del check. Deteniendo compras.")
                return False, current_page, message
            if "Order failed" in final_text:
                print("   ✗ Order failed (probablemente alguien la compró primero). Continuando con el siguiente artículo.")
                return True, current_page, message
            print("   Respuesta final:")
            print_message(final)
        else:
            print("   ✗ No hubo respuesta final")
    else:
        print("   (No se encontró botón check)")

    return True, current_page, message


# ============================================================
# FLUJO INICIAL CON REINTENTOS (COLOMBIA)
# ============================================================

async def start_flow(max_retries=3):
    """/start -> Country -> 5 -> COLOMBIA, con reintentos y debug."""
    for attempt in range(1, max_retries + 1):
        print(f"\n=== Intento {attempt}/{max_retries} ===")

        # [1] START
        print("[1] Enviando /start...")
        waiter = BotMessageWaiter()
        await waiter.prepare()
        await client.send_message(BOT, "/start")
        message = await waiter.wait(timeout=TIMEOUT)
        if not message:
            print("No se recibió respuesta a /start.")
            await asyncio.sleep(2)
            continue

        # [2] COUNTRY
        print("[2] Pulsando Country...")
        button = await find_button(message, "Country")
        if not button:
            print("No se encontró 'Country'. Botones disponibles:")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        message = await waiter.click_and_wait(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue

        # [3] 5
        print("[3] Pulsando 5...")
        button = await find_button(message, "5")
        if not button:
            print("No se encontró el botón '5'. Botones disponibles:")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        message = await waiter.click_and_wait(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue

        # [4] COLOMBIA
        print("[4] Pulsando COLOMBIA...")
        button = await find_button(message, "COLOMBIA")
        if not button:
            print("No se encontró COLOMBIA. Botones disponibles:")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        message = await waiter.click_and_wait(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue

        return message  # ✅ flujo completo

    return None


# ============================================================
# MAIN - ESTRATEGIA v8.1: comprar página por página
# ============================================================

async def main():
    print("\n>>> SCRIPT v8.1 (COLOMBIA) - COMPRA POR PÁGINA <<<")

    used_buttons.clear()

    print("Cargando productos.txt...")
    products = load_products()
    print(f"Productos cargados: {len(products)}")

    # Pasos 1-4 con reintentos
    message = await start_flow(max_retries=3)
    if not message:
        print("No se pudo completar el flujo inicial tras 3 intentos.")
        return
    print_message(message)

    current_page = 1
    total_bought = 0

    while True:
        print("\n" + "=" * 60)
        print(f"PÁGINA {current_page}")
        print("=" * 60)

        # 1. Filtrar artículos de ESTA página
        items = get_items(message)
        print(f"Artículos en esta página: {len(items)}")
        for item in items:
            print(item)

        if items:
            purchase_list = filter_page_items(items, products, current_page)
        else:
            purchase_list = []

        if purchase_list:
            print(f"\nCompras en esta página ({len(purchase_list)}):")
            for idx, rec in enumerate(purchase_list, 1):
                print(f"  {idx}. ID {rec['id']} | ${rec['price']:.2f} | Prioridad {rec['priority']}")

            # 2. Comprar TODO lo válido de esta página YA
            print("\n" + "-" * 60)
            print(f"COMPRANDO PÁGINA {current_page}")
            print("-" * 60)

            for rec in purchase_list:
                success, current_page, message = await purchase_item(rec, current_page, message)
                if not success:
                    print("\n*** Saldo insuficiente. Deteniendo todas las compras. ***")
                    return
                total_bought += 1
        else:
            print("No hay artículos válidos en esta página.")

        # 3. Pasar a la siguiente página
        next_btn = await find_button(message, "next page ➡️")
        if not next_btn:
            print("\nNo hay más páginas. Fin del recorrido.")
            break

        print("\nPasando a la siguiente página...")
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        new_msg = await waiter.click_and_wait(message, next_btn.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibió la siguiente página. Fin del recorrido.")
            break

        message = new_msg
        current_page += 1

        # Límite de seguridad (el bot siempre reporta 999999 páginas,
        # avanzamos solo mientras haya artículos "|")
        if current_page > MAX_PAGES:
            print(f"\nLímite de {MAX_PAGES} páginas alcanzado. Fin del recorrido.")
            break

    print("\n" + "=" * 60)
    print(f"PROCESO TERMINADO - {total_bought} compras realizadas")
    print("=" * 60)


# ============================================================
# LISTENER 24/7 + LOCK anti-ejecuciones concurrentes
# ============================================================

_is_running = False


async def trigger_flow():
    """Ejecuta el flujo completo cuando llega un mensaje del bot disparador."""
    global _is_running
    if _is_running:
        print(">>> Ya hay una ejecución en curso. Ignorando trigger. <<<")
        return
    _is_running = True
    try:
        print("\n" + "=" * 60)
        print(">>> TRIGGER RECIBIDO - INICIANDO FLUJO COMPLETO <<<")
        print("=" * 60)
        await main()
    except Exception as e:
        print(f">>> ERROR durante la ejecución: {e!r} <<<")
    finally:
        _is_running = False
        print(">>> Flujo terminado. Esperando próximo trigger... <<<")


@client.on(events.NewMessage())
async def trigger_handler(event):
    # Validamos el remitente manualmente (más robusto que from_users=)
    sender = await event.get_sender()
    username = (getattr(sender, "username", None) or "").lower()
    if username != TRIGGER_USERNAME:
        return
    await trigger_flow()


# ============================================================
# ARRANQUE CON AUTO-RECONECCIÓN
# ============================================================

async def run_forever():
    global BOT_ID
    while True:
        try:
            if not SESSION_STRING:
                raise RuntimeError("TELEGRAM_SESSION no está definida en las variables de entorno")

            await client.start()
            me = await client.get_me()
            if me is None:
                raise RuntimeError("La sesión no está autorizada. Regenera TELEGRAM_SESSION.")

            # Resolver el ID numérico del bot UNA sola vez (clave para los handlers)
            if BOT_ID is None:
                bot_entity = await client.get_entity(BOT)
                BOT_ID = bot_entity.id
                print(f">>> ID de {BOT} resuelto: {BOT_ID} <<<")

            print(">>> SERVICIO v8.1 ACTIVO (COL) - escuchando triggers 24/7 <<<")
            print(f">>> Logueado como: {me.first_name} (@{me.username}) <<<")
            print(f">>> Disparador: @{TRIGGER_USERNAME} <<<")

            await client.run_until_disconnected()

        except Exception as e:
            print(f">>> CONEXIÓN CAÍDA: {e!r} <<<")
            print(">>> Reintentando en 15 segundos... <<<")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(15)


print(">>> Iniciando servicio... <<<")
client.loop.run_until_complete(run_forever())
