import asyncio
import os
import time
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ============================================================
# CONFIGURACION (variables de entorno para Railway)
# ============================================================
API_ID = int(os.environ.get("API_ID", 21585700))
API_HASH = os.environ.get("API_HASH", "34aea5894918c1155fc0e8d432396880")

BOT = "@Globalccvs_Bot"
TRIGGER_USERNAME = "ccscards_bot"

SESSION_STRING = os.environ.get("TELEGRAM_SESSION", "").strip()

PRODUCTOS_FILE = "productos.txt"
MAX_PRICE = float(os.environ.get("MAX_PRICE", 5.0))

TIMEOUT = 45
POLL_INTERVAL = 0.5
TRIGGER_POLL_INTERVAL = 20
MAX_PAGES = 300

if not os.path.exists(PRODUCTOS_FILE):
    with open(PRODUCTOS_FILE, "w", encoding="utf-8") as f:
        f.write(os.environ.get("PRODUCTOS_CONTENT", ""))


client = TelegramClient(
    StringSession(SESSION_STRING) if SESSION_STRING else "telegram_session",
    API_ID,
    API_HASH
)

INSUFFICIENT_MSG = (
    "Current user's account balance is insufficient. "
    "Please return to the homepage to recharge or adjust the amount."
)

used_buttons = set()

BOT_ID = None
TRIGGER_BOT_ID = None
_last_trigger_id = 0
_is_running = False


# ============================================================
# HELPERS DE HISTORIAL (motor de deteccion por polling)
# ============================================================

def _snapshot(msg):
    btns = []
    if msg.buttons:
        for row in msg.buttons:
            for b in row:
                btns.append(b.text)
    return (msg.text or "", tuple(btns))


async def get_baseline():
    messages = await client.get_messages(BOT, limit=3)
    for m in messages:
        if not m.out:
            return m.id, _snapshot(m)
    return 0, ("", tuple())


async def wait_for_response(baseline_id, baseline_sig, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            messages = await client.get_messages(BOT, limit=3)
        except Exception as e:
            print(f"   [poll] Error consultando historial: {e}")
            await asyncio.sleep(1)
            continue

        for m in messages:
            if m.out:
                continue

            if m.id > baseline_id:
                print(f"   [poll] Nuevo mensaje id={m.id} detectado (baseline={baseline_id})")
                return m

            if m.id == baseline_id and baseline_sig is not None:
                sig = _snapshot(m)
                if sig != baseline_sig:
                    print(f"   [poll] Mensaje baseline id={m.id} fue EDITADO")
                    return m

        await asyncio.sleep(POLL_INTERVAL)

    print(f"   [poll] Timeout de {timeout}s sin nueva respuesta")
    return None


async def click_and_wait(message, text, timeout=TIMEOUT):
    baseline_id, baseline_sig = await get_baseline()

    click_task = asyncio.create_task(message.click(text=text))
    try:
        await asyncio.wait_for(click_task, timeout=5)
    except Exception as e:
        print(f"   [click] Error haciendo clic: {e!r}")

    return await wait_for_response(baseline_id, baseline_sig, timeout)


async def send_and_wait(text, timeout=TIMEOUT):
    baseline_id, baseline_sig = await get_baseline()
    await client.send_message(BOT, text)
    return await wait_for_response(baseline_id, baseline_sig, timeout)


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
# MENSAJES / ARTICULOS / BOTONES
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
    parts item_text.split("|")
    if len(parts) < 2:
        return None
    price_text = parts[1].strip()
    price_text = price_text.replace("💵", "").replace("$", "").replace("USD", "").strip()
    try:
        return float(price_text.replace(",", "."))
    except ValueError:
        return None


# ============================================================
# FILTRO DE ARTICULOS DE UNA SOLA PAGINA
# ============================================================

def filter_page_items(items, products, page_num):
    product_ids = {p["id"]: p["priority"] for p in products}
    valid = []

    print(f"\n   [debug] Analizando {len(items)} articulos de la pagina {page_num}...")

    for item in items:
        item_id = extract_id(item)
        price = extract_price(item)

        if item_id is None or price is None:
            print(f"   [debug] Pag {page_num} | ilegible | ✗ RECHAZADO: {item!r}")
            continue
        if item_id not in product_ids:
            print(f"   [debug] Pag {page_num} | {item_id} | ${price} | ✗ NO esta en productos.txt")
            continue
        if price <= 9.1:
            print(
                f"   [debug] Pág {page_num} | {item_id} | "
                f"${price:.2f} | ✗ precio <= $10.00"
            )
            continue

        print(f"   [debug] Pag {page_num} | {item_id} | ${price:.2f} | ✓ VALIDO")
        valid.append({
            "id": item_id,
            "item": item,
            "price": price,
            "priority": product_ids[item_id],
            "page": page_num
        })

    seen = set()
    unique_list = []
    for rec in sorted(valid, key=lambda x: x["price"]):
        if rec["item"] not in seen:
            seen.add(rec["item"])
            unique_list.append(rec)
    unique_list.sort(key=lambda x: (x["priority"], x["price"]))
    return unique_list


# ============================================================
# NAVEGACION
# ============================================================

async def navigate_to_page(current_page, target_page, message):
    while current_page < target_page:
        next_btn = await find_button(message, "next page ➡️")
        if not next_btn:
            print("No se encontro boton next page")
            return None
        t0 = time.perf_counter()
        new_msg = click_and_wait(message, next_btn.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibio la pagina siguiente")
            return None
        message = new_msg
        current_page += 1

    while current_page > target_page:
        prev_btn = await find_button(message, "Previous")
        if not prev_btn:
            print("No se encontro boton Previous")
            return None
        t0 = time.perf_counter()
        new_msg = await click_and_wait(message, prev_btn.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibio la pagina anterior")
            return None
        message = new_msg
        current_page -= 1

    return message


# ============================================================
# COMPRA DE UN ARTICULO
# ============================================================

async def purchase_item(record, current_page, message):
    print(f"\n>>> Comprando: {record['item']} (pagina {record['page']}, prioridad {record['priority']})")

    if current_page != record["page"]:
        print(f"Navegando de pagina {current_page} a {record['page']}...")
        message = await navigate_to_page(current_page, record["page"], message)
        if not message:
            return True, current_page, message
        current_page = record["page"]

    if not message.buttons:
        print("   ✗ Mensaje sin botones")
        return True, current_page, message

    found = False
    for row in message.buttons:
        for button in row:
            if button.text.strip() == record["item"].strip():
                found = True
    if not found:
        print("   ✗ El boton del articulo ya no existe (probablemente comprado)")
        return True, current_page, message

    t0 = time.perf_counter()
    response = await click_and_wait(message, record["item"], timeout=TIMEOUT)
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
        print   -> Boton check encontrado, haciendo clic...")
        t0 = time.perf_counter()
        final = await click_and_wait(response, check_btn.text, timeout=TIMEOUT)
        print(f"   (Respuesta final en {time.perf_counter() - t0:.2f}s)")
        if final:
            final_text = final.text or ""
            if INSUFFICIENT_MSG in final_text:
                print("   ✗ Saldo insuficiente despues del check. Deteniendo compras.")
                return False, current_page, message
            if "Order failed" in final_text:
                print("   ✗ Order failed (probablemente alguien la compro primero). Continuando.")
                return True, current_page, message
            print("   Respuesta final:")
            print_message(final)
        else:
            print("   ✗ No hubo respuesta final")
    else:
        print("   (No se encontro boton check)")

    return True, current_page, message


# ============================================================
# FLUJO INICIAL CON REINTENTOS (COLOMBIA)
# ============================================================

async def start_flow(max_retries=3):
    for attempt in range(1, max_retries + 1):
        print(f"\n=== Intento {attempt}/{max_retries} ===")

        print("[1] Enviando /start...")
        t0 = time.perf_counter()
        message = await send_and_wait("/start", timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf() - t0:.2f}s)")
        if not message:
            print("No se recibio respuesta a /start.")
            await asyncio.sleep(2)
            continue

        print("[2] Pulsando Country...")
        button = await find_button(message, "Country")
        if not button:
            print("No se encontro 'Country'. Botones disponibles:")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
 t0 =.perf_counter()
        message = await click_and_wait(message button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {.perf_counter - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue

        print("[3] Pulsando ...")
        button await find_button, "5")
        if not button:
            print("No se encontro el boton '5'. Botones disponibles:")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        t0 = time.perf_counter()
        message = await click_and_wait(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue

        print("[4] Pulsando COLOMBIA...")
        button = await find_button(message, "COSTA RICA")
        if not button:
            print("No se encontro COLOMBIA. Botones disponibles:")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        t0 = time.perf_counter()
        message = await click_and_wait(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue

        return message

    return None


# ============================================================
# MAIN - v8.4: compra pagina por pagina (polling)
# ============================================================

async def main():
    print("\n>>> SCRIPT v8.4 (COLOMBIA) - POLLING ENGINE <<<")

 used_buttons.clear    print("argando productos.txt...")
    products = load_products()
 print(f" cargados: {len(products)}")

    = await start(max_retries=3)
    if not message:
        print(" se pudo complet el flujo tras 3 intentos.")
        return
    print_message(message)

    current_page = 1
    total_bought = 0

    while True:
        print("\n" + "=" * 60)
        print(f"PAGINA {current_page}")
        print("=" * 60)

        items = get_items(message)
        print(f"Articulos en esta pagina {len(items)}")
        for item in items:
            print(item)

        if items:
            purchase_list = filter_page_items(items, products, current_page)
        else:
            purchase_list = []

        if purchase_list:
            print(f"\nCompras en esta pagina ({len(purchase_list)}):")
            for idx, rec in enumerate(purchase_list, 1):
                print(f"  {idx}. ID {rec['id']} | ${rec['price']:.2f} | Prioridad {rec['priority']}")

            print("\n" + "-" * 60)
            print(f"COMPRANDO PAGINA {current_page}")
            print("-" * 60)

            for rec in purchase_list:
                success, current_page, message = await purchase_item(rec, current_page, message)
                if not success:
                    print("\n*** Saldo insuficiente. Deteniendo todas las compras. ***")
                    return
                total_bought += 1
        else:
            print("No hay articulos validos en esta pagina.")

        next_btn = await find_button(message, "next page ➡️")
        if not next_btn:
            print("\nNo mas paginas Fin del recorrido.")
            break

        print("\nPasando a la siguiente pagina...")
        t = time.per_counter()
       _msg = await click_wait(message,_btn.text,=TIMEOUT        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
 print("No se recibio la siguiente pagina. Fin del recorrido.")
            break

        message = new_msg
       _page += 1

        if current_page > MAX_PAGES:
            print(f"\nLimite de {MAX_PAGES} paginas alcanzado. Fin del recorrido.")
            break

    print("\n" + "=" * 60)
    print(f"PROCESO TERMINADO - {total_bought} compras realizadas")
    print("=" * 60)


# ============================================================
# TRIGGER: eventos + polling de respaldo + LOCK
# ============================================================

async def trigger_flow():
    global _is_running
    if _is_running:
        print(">>> Ya hay una ejecucion en curso. Ignorando trigger. <<<")
        return
    _is_running = True
    try:
 print("\n" + "=" * 60)
        print(">>> TRIGGER RECIBIDO - INICIANDO FLUJO COMPLETO <<<")
        print("=" * 60)
        await main()
    except Exception as e:
        print(f">>> ERROR durante la ejecucion: {e!r} <<<")
    finally:
        _is_running = False
        print(">>> Flujo terminado. Esperando proximo trigger... <<<")


@client.on(events.NewMessage())
async def trigger_handler(event):
    try:
        sender = await event.get_sender()
        username = (getattr(sender, "username", None) or "").lower()
        if username != TRIGGER_USERNAME:
            return
        print(f">>> TRIGGER detectado por EVENTO de @{username} <<<")
        asyncio.create_task(trigger_flow())
    except Exception as e:
        print(f">>> ERROR en trigger_handler: {e!r} <<<")


async def _check_trigger_history():
    global _last_trigger_id
    try:
        messages = await client.get_messages(TRIGGER_USERNAME, limit=5)
        now = time.time()
        for m in messages:
            if m.out:
                continue
 age = now - m.date.timestamp            if age < 90 and m.id > _last_trigger_id:
                _last_trigger_id = m.id
                print(f">>> TRIGGER detectado por POLLING (id={m.id}, edad {age:.0f}s) <<<")
                asyncio.create_task(trigger_flow())
                return
    except Exception as e:
        print(f">>> [trigger-poll] Error: {e!r} <<<")


async def _trigger_poll_loop():
    while True:
        try:
            await _check_trigger_history()
        except Exception as e:
            print(f">>> [trigger-poll] Error en loop: {e!r} <<<")
        await asyncio.sleep(TRIGGER_POLL_INTERVAL)


# ============================================================
# ARRANQUE CON AUTO-RECONEXION
# ============================================================

async def run_forever():
    global BOT_ID
    global _last_trigger_id
    while True:
        try:
            if not SESSION_STRING:
                raise RuntimeError("TELEGRAM_SESSION no esta definida en las variables de entorno")

            await client.start()
            me = await client.get_me()
            if me is None:
                raise RuntimeError("La sesion no esta autorizada. Regenera TELEGRAM_SESSION.")

            if BOT_ID is None:
                try:
                    bot_entity = await client.get_entity(BOT)
                    BOT_ID = bot_entity.id
                    print(f">>> ID de {BOT} resuelto: {BOT_ID} <<<")
                except Exception as e:
                    print(f">>> No se pudo resolver ID de {BOT}: {e!r} <<<")

            try:
                trigger_messages = await client.get_messages(TRIGGER_USERNAME, limit=1)
                if trigger_messages:
                    _last_trigger_id = trigger_messages[0].id
                    print(f">>> Baseline de trigger: id={_last_trigger_id} <<<")
            except Exception as e:
                print(f">>> No se pudo establecer baseline de trigger: {e!r} <<<")

            print(">>> SERVICIO v8.4 ACTIVO (COL) - polling + evento dual - 24/7 <<<")
            print(f">>> Logueado como: {me.first_name} (@{me.username}) <<<")
            print(f">>> Disparador: @{TRIGGER_USERNAME} <<<")

            asyncio.create_task(_trigger_poll_loop())

            await client.run_until_disconnected()

        except Exception as e:
            print(f">>> CONEXION CAIDA: {e!r} <<<")
            print(">>> Reintentando en 15 segundos... <<<")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(15)


print(">>> Iniciando servicio... <<<")
client.loop.run_until_complete(run_forever())
