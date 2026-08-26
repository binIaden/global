import asyncio
import os
import time
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ============================================================
# CONFIGURACIÓN (variables de entorno para Railway)
# ============================================================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")

BOT = "@Globalccvs_Bot"

# Usuario/bot cuyo mensaje dispara el flujo de compra
TRIGGER_USER = "@CcsCards_Bot"  # ← configurar en Railway

# StringSession generada previamente (variable de entorno TELEGRAM_SESSION)
SESSION_STRING = os.environ.get("TELEGRAM_SESSION")

# productos.txt: contenido subido como variable de entorno PRODUCTOS_CONTENT
PRODUCTOS_FILE = "productos.txt"
MAX_PRICE = float(5.0)

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
            msg = event.message
            print(f"   [debug] NewMessage recibido a los {time.perf_counter()-self._t0:.2f}s "
                  f"(id={msg.id}, out={msg.out}, texto={str(msg.text)[:40]!r})")
            if not msg.out:
                if not self.future.done():
                    self.future.set_result(msg)
                    print("   [debug] >>> FUTURE RESUELTO <<<")

        async def on_edit(event):
            msg = await event.get_message()
            print(f"   [debug] MessageEdited recibido a los {time.perf_counter()-self._t0:.2f}s "
                  f"(id={msg.id}, out={msg.out}, texto={str(msg.text)[:40]!r})")
            if not msg.out:
                if not self.future.done():
                    self.future.set_result(msg)
                    print("   [debug] >>> FUTURE RESUELTO (edit) <<<")

        self.handler = on_new
        self.handler_edit = on_edit
        client.add_event_handler(on_new, events.NewMessage(from_users=BOT))
        client.add_event_handler(on_edit, events.MessageEdited(from_users=BOT))

    def _unregister_handlers(self):
        if self.handler:
            client.remove_event_handler(self.handler, events.NewMessage(from_users=BOT))
            self.handler = None
        if self.handler_edit:
            client.remove_event_handler(self.handler_edit, events.MessageEdited(from_users=BOT))
            self.handler_edit = None

    async def _poll_fallback(self):
        try:
            messages = await client.get_messages(BOT, limit=3)
            for m in messages:
                if not m.out:
                    return m
        except Exception as e:
            print(f"   [fallback] Error: {e}")
        return None

    async def click_and_wait(self, message, text, timeout=30):
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

    async def wait(self, timeout=30, fallback=True):
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
    print("" + "=" * 60)
    print("ID:", message.id)
    print("TEXTO:")
    print(message.text or "(sin texto)")
    if message.buttons:
        print("BOTONES:")
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
# RECOLECCIÓN Y LISTA DE COMPRA
# ============================================================

def build_purchase_list(page_data, products):
    purchase_list = []
    product_ids = {p["id"]: p["priority"] for p in products}

    total = sum(len(p["items"]) for p in page_data)
    print(f"   [debug] Analizando {total} artículos...")
    print(f"   [debug] IDs en productos.txt: {len(product_ids)}")

    for page_info in page_data:
        page_num = page_info["page"]
        for item in page_info["items"]:
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
            purchase_list.append({
                "id": item_id,
                "item": item,
                "price": price,
                "priority": product_ids[item_id],
                "page": page_num
            })

    # Deduplicar por texto exacto del botón; más barato primero dentro de cada prioridad
    seen = set()
    unique_list = []
    for rec in sorted(purchase_list, key=lambda x: x["price"]):
        if rec["item"] not in seen:
            seen.add(rec["item"])
            unique_list.append(rec)
    unique_list.sort(key=lambda x: (x["priority"], x["page"]))
    return unique_list


# ============================================================
# NAVEGACIÓN Y COMPRA
# ============================================================

async def navigate_to_page(current_page, target_page, message):
    while current_page < target_page:
        next_btn = await find_button(message, "next page ➡️")
        if not next_btn:
            print("No se encontró botón next page")
            return None
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        new_msg = await waiter.click_and_wait(message, next_btn.text, timeout=30)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibió la página siguiente")
            return None
        message = new_msg
        current_page += 1

    while current_page > target_page:
        last_btn = await find_button(message, "⬅️ last page")
        if not last_btn:
            print("No se encontró botón last page")
            return None
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        new_msg = await waiter.click_and_wait(message, last_btn.text, timeout=30)
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
    print(f">>> Comprando: {record['item']} (página {record['page']}, prioridad {record['priority']})")

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
    response = await waiter.click_and_wait(message, record["item"], timeout=30)
    print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
    if response is None:
        print("   ✗ No hubo respuesta")
        return True, current_page, message

    used_buttons.add(record["item"])

    if response.text and response.text.strip() == INSUFFICIENT_MSG:
        print("   ✗ Saldo insuficiente detectado. Deteniendo compras.")
        return False, current_page, message

    print("   Respuesta del bot:")
    print_message(response)

    check_btn = await find_check_button(response)
    if check_btn:
        print("   -> Botón check encontrado, haciendo clic...")
        waiter = BotMessageWaiter()
        final = await waiter.click_and_wait(response, check_btn.text, timeout=30)
        if final:
            if final.text and final.text.strip() == INSUFFICIENT_MSG:
                print("   ✗ Saldo insuficiente después del check. Deteniendo compras.")
                return False, current_page, message
            print("   Respuesta final:")
            print_message(final)
        else:
            print("   ✗ No hubo respuesta final")
    else:
        print("   (No se encontró botón check)")

    return True, current_page, message


# ============================================================
# FLUJO INICIAL CON REINTENTOS
# ============================================================

async def start_flow(max_retries=3):
    """/start -> Country -> 5 -> COLOMBIA, con reintentos y debug."""
    for attempt in range(1, max_retries + 1):
        print(f"=== Intento {attempt}/{max_retries} ===")

        # [1] START
        print("[1] Enviando /start...")
        waiter = BotMessageWaiter()
        await waiter.prepare()
        await client.send_message(BOT, "/start")
        message = await waiter.wait(timeout=30)
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
        message = await waiter.click_and_wait(message, button.text, timeout=30)
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
        message = await waiter.click_and_wait(message, button.text, timeout=30)
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
        message = await waiter.click_and_wait(message, button.text, timeout=30)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue

        return message  # ✅ flujo completo

    return None


# ============================================================
# MAIN
# ============================================================

async def main():
    print(">>> SCRIPT v7 - SERVICIO 24/7 <<<")

    used_buttons.clear()  # limpiar entre ejecuciones

    print("Cargando productos.txt...")
    products = load_products()
    print(f"Productos cargados: {len(products)}")

    # Pasos 1-4 con reintentos
    message = await start_flow(max_retries=3)
    if not message:
        print("No se pudo completar el flujo inicial tras 3 intentos.")
        return
    print_message(message)

    # ========================================================
    # RECOLECCIÓN DE TODAS LAS PÁGINAS
    # ========================================================
    print("" + "=" * 60)
    print("RECOLECTANDO TODAS LAS PÁGINAS")
    print("=" * 60)

    page_data = []
    page = 1
    seen_pages = set()

    while True:
        print(f"--- Página {page} ---")
        items = get_items(message)

        if not items and page > 1:
            print("Página vacía, deteniendo recolección.")
            break

        print(f"Artículos en esta página: {len(items)}")
        for item in items:
            print(item)

        page_data.append({"page": page, "items": items})

        signature = tuple(items)
        if signature in seen_pages:
            print("Página repetida, deteniendo recolección.")
            page_data.pop()
            break
        seen_pages.add(signature)

        next_btn = await find_button(message, "next page ➡️")
        if not next_btn:
            print("No hay más páginas.")
            break

        print("Pasando a siguiente página...")
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        new_msg = await waiter.click_and_wait(message, next_btn.text, timeout=30)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibió la siguiente página.")
            break
        message = new_msg
        page += 1

    total_pages = len(page_data)
    print(f"Total de páginas recolectadas: {total_pages}")

    # ========================================================
    # LISTA DE COMPRA
    # ========================================================
    purchase_list = build_purchase_list(page_data, products)
    print(f"Artículos válidos para comprar: {len(purchase_list)}")

    if not purchase_list:
        print("No hay artículos para comprar.")
        return

    print("Lista de compra (orden de prioridad):")
    for idx, rec in enumerate(purchase_list, 1):
        print(f"{idx}. Página {rec['page']} | ID {rec['id']} | Precio ${rec['price']:.2f} | Prioridad {rec['priority']}")

    # ========================================================
    # VOLVER A LA PRIMERA PÁGINA
    # ========================================================
    print("Volviendo a la primera página...")
    current_page = page
    while current_page > 1:
        last_btn = await find_button(message, "⬅️ last page")
        if not last_btn:
            print("No se encontró botón last page, no se puede volver.")
            return
        waiter = BotMessageWaiter()
        t0 = time.perf_counter()
        new_msg = await waiter.click_and_wait(message, last_btn.text, timeout=30)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibió la página anterior.")
            return
        message = new_msg
        current_page -= 1

    # ========================================================
    # COMPRAR
    # ========================================================
    print("" + "=" * 60)
    print("INICIANDO COMPRAS")
    print("=" * 60)

    for rec in purchase_list:
        success, current_page, message = await purchase_item(rec, current_page, message)
        if not success:
            print("*** Saldo insuficiente. Deteniendo todas las compras. ***")
            break

    print("" + "=" * 60)
    print("PROCESO DE COMPRA TERMINADO")
    print("=" * 60)


# ============================================================
# LISTENER 24/7 + LOCK anti-ejecuciones concurrentes
# ============================================================

_is_running = False


@client.on(events.NewMessage(from_users=TRIGGER_USER))
async def trigger_handler(event):
    """Cuando el bot disparador envía un mensaje, ejecuta el flujo completo."""
    global _is_running
    if _is_running:
        print(">>> Ya hay una ejecución en curso. Ignorando trigger. <<<")
        return
    _is_running = True
    try:
        print("" + "=" * 60)
        print(f">>> TRIGGER RECIBIDO de {TRIGGER_USER} - INICIANDO FLUJO COMPLETO <<<")
        print("=" * 60)
        await main()
    except Exception as e:
        print(f">>> ERROR durante la ejecución: {e!r} <<<")
    finally:
        _is_running = False
        print(">>> Flujo terminado. Esperando próximo trigger... <<<")


# ============================================================
# EJECUTAR
# ============================================================

print(">>> SERVICIO v7 ACTIVO - escuchando triggers 24/7 <<<")
print(f">>> Disparador: {TRIGGER_USER} <<<")
client.run_until_disconnected()
