import asyncio
import os
import time
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ============================================================
# CONFIGURACIÓN
# ============================================================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")

BOT = "@Globalccvs_Bot"
TRIGGER_USERNAME = "ccscards_bot"

SESSION_STRING = os.environ.get("TELEGRAM_SESSION", "").strip()

PRODUCTOS_FILE = "productos.txt"
MAX_PRICE = 6.0  # <--- aumentado a 6.0

TIMEOUT = 45  # puedes subir a 60 si el bot es muy lento
POLL_INTERVAL = 0.5
MAX_PAGES = 300
MAX_RETRIES = 2  # reintentos por cada acción

if not os.path.exists(PRODUCTOS_FILE):
    with open(PRODUCTOS_FILE, "w", encoding="utf-8") as f:
        f.write(os.environ.get("PRODUCTOS_CONTENT", ""))

client = TelegramClient(
    StringSession(SESSION_STRING) if SESSION_STRING else "telegram_session",
    API_ID,
    API_HASH
)

INSUFFICIENT_MSG = "Current user's account balance is insufficient. Please return to the homepage to recharge or adjust the amount."

used_buttons = set()

BOT_ID = None
TRIGGER_ID = None

refund_detected = False
refund_event = asyncio.Event()

# ============================================================
# POLLING ENGINE (con mejoras de logs)
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

async def wait_for_response(baseline_id, baseline_sig, timeout=TIMEOUT, attempt=1):
    """Espera respuesta del bot con reintentos internos."""
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
                print(f"   [poll] Nuevo mensaje id={m.id}")
                return m
            if m.id == baseline_id and baseline_sig is not None:
                sig = _snapshot(m)
                if sig != baseline_sig:
                    print(f"   [poll] Mensaje editado id={m.id}")
                    return m
        await asyncio.sleep(POLL_INTERVAL)
    print(f"   [poll] Timeout tras {timeout}s (intento {attempt})")
    return None

async def click_and_wait_with_retry(message, text, timeout=TIMEOUT, max_retries=MAX_RETRIES):
    """Hace clic en un botón y espera respuesta, reintentando si falla."""
    for attempt in range(1, max_retries + 1):
        baseline_id, baseline_sig = await get_baseline()
        print(f"   [click] Intento {attempt}/{max_retries} para '{text}'")
        click_task = asyncio.create_task(message.click(text=text))
        try:
            await asyncio.wait_for(click_task, timeout=5)
        except asyncio.TimeoutError:
            print(f"   [click] Timeout al hacer clic (intento {attempt})")
        except Exception as e:
            print(f"   [click] Error al hacer clic: {e!r} (intento {attempt})")

        # Esperar respuesta
        response = await wait_for_response(baseline_id, baseline_sig, timeout, attempt)
        if response is not None:
            return response
        # Si no hubo respuesta, esperar un poco y reintentar
        if attempt < max_retries:
            print(f"   [reintento] Esperando 2s antes de reintentar...")
            await asyncio.sleep(2)
    print(f"   [click] Fallaron todos los reintentos para '{text}'")
    return None

async def send_and_wait(text, timeout=TIMEOUT):
    """Envía un mensaje y espera respuesta (sin reintentos)."""
    baseline_id, baseline_sig = await get_baseline()
    await client.send_message(BOT, text)
    return await wait_for_response(baseline_id, baseline_sig, timeout)

# ============================================================
# UTILIDADES (sin cambios)
# ============================================================

def _dump_buttons(message):
    if message.buttons:
        for r_i, row in enumerate(message.buttons):
            for b in row:
                print(f"   [{r_i}] {b.text!r}")
    else:
        print("   (sin botones) Texto:", repr((message.text or "")[:120]))

def load_products():
    products = []
    print("\n[DEBUG] Leyendo productos.txt...")
    with open(PRODUCTOS_FILE, "r", encoding="utf-8") as f:
        content = f.read()
        print(f"[DEBUG] Contenido (primeros 200 chars):\n{content[:200]}")
        f.seek(0)
        for line_number, line in enumerate(f, 1):
            product_id = line.strip()
            if not product_id:
                continue
            products.append({"id": product_id, "priority": line_number})
    print(f"[DEBUG] IDs cargados (primeros 10): {[p['id'] for p in products[:10]]}")
    return products

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
# FILTRO DE ARTÍCULOS
# ============================================================

def filter_page_items(items, products, page_num):
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
            print(f"   [debug] Pág {page_num} | {item_id} | ${price:.2f} | ✗ precio > {MAX_PRICE}")
            continue
        print(f"   [debug] Pág {page_num} | {item_id} | ${price:.2f} | ✓ VÁLIDO")
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
# NAVEGACIÓN Y COMPRA (con reintentos y logs mejorados)
# ============================================================

async def navigate_to_page(current_page, target_page, message):
    while current_page < target_page:
        next_btn = await find_button(message, "next page ➡️")
        if not next_btn:
            print("No se encontró botón next page")
            return None
        t0 = time.perf_counter()
        new_msg = await click_and_wait_with_retry(message, next_btn.text, timeout=TIMEOUT)
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
        t0 = time.perf_counter()
        new_msg = await click_and_wait_with_retry(message, prev_btn.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not new_msg:
            print("No se recibió la página anterior")
            return None
        message = new_msg
        current_page -= 1
    return message

async def purchase_item(record, current_page, message):
    print(f"\n>>> Comprando: {record['item']} (página {record['page']}, prioridad {record['priority']})")

    if current_page != record["page"]:
        print(f"Navegando de página {current_page} a {record['page']}...")
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
        print("   ✗ El botón del artículo ya no existe (probablemente comprado)")
        return True, current_page, message

    # --- PASO 1: Hacer clic en el artículo con reintentos ---
    print(f"   [intento] Haciendo clic en artículo...")
    t0 = time.perf_counter()
    response = await click_and_wait_with_retry(message, record["item"], timeout=TIMEOUT)
    elapsed = time.perf_counter() - t0
    print(f"   (Respuesta en {elapsed:.2f}s)")
    if response is None:
        print("   ✗ No hubo respuesta al clic en el artículo (tras reintentos). Saltando.")
        return True, current_page, message

    used_buttons.add(record["item"])

    # Saldo insuficiente
    if response.text and INSUFFICIENT_MSG in response.text:
        print("   ✗ Saldo insuficiente. Saltando este artículo...")
        return True, current_page, message

    print("   Respuesta del bot tras clic en artículo:")
    print_message(response)

    # --- PASO 2: Buscar y hacer clic en el botón check ---
    check_btn = await find_check_button(response)
    if not check_btn:
        print("   (No se encontró botón check, compra completada sin check)")
        return True, current_page, message

    print("   -> Botón check encontrado, haciendo clic con reintentos...")
    t0 = time.perf_counter()
    final = await click_and_wait_with_retry(response, check_btn.text, timeout=TIMEOUT)
    elapsed = time.perf_counter() - t0
    print(f"   (Respuesta final en {elapsed:.2f}s)")

    if final is None:
        print("   ✗ No hubo respuesta final al check. Saltando.")
        return True, current_page, message

    final_text = final.text or ""

    if INSUFFICIENT_MSG in final_text:
        print("   ✗ Saldo insuficiente después del check. Saltando...")
        return True, current_page, message

    if "Order failed" in final_text:
        print("   ✗ Order failed (probablemente alguien la compró primero). Saltando.")
        return True, current_page, message

    print("   Respuesta final (compra exitosa o confirmación):")
    print_message(final)
    return True, current_page, message

# ============================================================
# FLUJO INICIAL (sin cambios, pero usa las funciones mejoradas)
# ============================================================

async def start_flow(max_retries=3):
    for attempt in range(1, max_retries + 1):
        print(f"\n=== Intento {attempt}/{max_retries} ===")
        print("[1] Enviando /start...")
        t0 = time.perf_counter()
        message = await send_and_wait("/start", timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            print("No se recibió respuesta a /start.")
            await asyncio.sleep(2)
            continue
        print("[2] Pulsando Country...")
        button = await find_button(message, "Country")
        if not button:
            print("No se encontró 'Country'.")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        t0 = time.perf_counter()
        message = await click_and_wait_with_retry(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue
        print("[3] Pulsando 5...")
        button = await find_button(message, "5")
        if not button:
            print("No se encontró '5'.")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        t0 = time.perf_counter()
        message = await click_and_wait_with_retry(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue
        print("[4] Pulsando COLOMBIA...")
        button = await find_button(message, "COLOMBIA")
        if not button:
            print("No se encontró COLOMBIA.")
            _dump_buttons(message)
            await asyncio.sleep(2)
            continue
        t0 = time.perf_counter()
        message = await click_and_wait_with_retry(message, button.text, timeout=TIMEOUT)
        print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
        if not message:
            await asyncio.sleep(2)
            continue
        return message
    return None

# ============================================================
# MAIN - con bucle de refund y mejoras de logs
# ============================================================

async def main():
    global refund_detected

    print("\n>>> SCRIPT v8.5 (COLOMBIA) - CON REINTENTOS Y REFUND DETECTOR <<<")

    while True:
        used_buttons.clear()
        refund_detected = False

        print("Cargando productos.txt...")
        products = load_products()
        if not products:
            print("⚠️ No se cargaron productos. Verifica PRODUCTOS_CONTENT.")
            return

        message = await start_flow(max_retries=3)
        if not message:
            print("No se pudo completar el flujo inicial.")
            return
        print_message(message)

        current_page = 1
        total_bought = 0

        while True:
            print("\n" + "=" * 60)
            print(f"PÁGINA {current_page}")
            print("=" * 60)
            items = get_items(message)
            print(f"Artículos en esta página: {len(items)}")
            for item in items:
                print(item)

            purchase_list = filter_page_items(items, products, current_page) if items else []

            if purchase_list:
                print(f"\nCompras en esta página ({len(purchase_list)}):")
                for idx, rec in enumerate(purchase_list, 1):
                    print(f"  {idx}. ID {rec['id']} | ${rec['price']:.2f} | Prioridad {rec['priority']}")
                print("\n" + "-" * 60)
                print(f"COMPRANDO PÁGINA {current_page}")
                print("-" * 60)
                for rec in purchase_list:
                    success, current_page, message = await purchase_item(rec, current_page, message)
                    if not success:
                        print("⚠️ Error crítico en purchase_item")
                    total_bought += 1
            else:
                print("No hay artículos válidos en esta página.")

            next_btn = await find_button(message, "next page ➡️")
            if not next_btn:
                print("\nNo hay más páginas. Fin del recorrido de páginas.")
                break

            print("\nPasando a la siguiente página...")
            t0 = time.perf_counter()
            new_msg = await click_and_wait_with_retry(message, next_btn.text, timeout=TIMEOUT)
            print(f"   (Respuesta en {time.perf_counter() - t0:.2f}s)")
            if not new_msg:
                print("No se recibió la siguiente página. Fin del recorrido.")
                break
            message = new_msg
            current_page += 1
            if current_page > MAX_PAGES:
                print(f"\nLímite de {MAX_PAGES} páginas alcanzado.")
                break

        print("\n" + "=" * 60)
        print(f"Recorrido completado - {total_bought} compras intentadas")
        print("=" * 60)

        if refund_detected:
            print("✅ Se detectó al menos un refund. Reiniciando proceso inmediatamente...")
            continue

        print("⏳ No hubo refunds durante las compras. Esperando hasta 2 minutos por nuevos refunds...")
        try:
            await asyncio.wait_for(refund_event.wait(), timeout=120)
            print("✅ Refund detectado durante la espera. Reiniciando proceso...")
            refund_event.clear()
            continue
        except asyncio.TimeoutError:
            print("⏰ Tiempo de espera agotado. No se detectaron refunds en 2 minutos.")
            break

    print(">>> Flujo de compras finalizado. Volviendo a esperar trigger...")

# ============================================================
# HANDLER DE REFUNDS
# ============================================================

async def refund_handler(event):
    global refund_detected
    if BOT_ID is not None and event.sender_id != BOT_ID:
        return
    if event.message.out:
        return
    text = event.message.text or ""
    if "refund" in text.lower() and "account balance" in text.lower():
        print(f"\n💰 REFUND DETECTADO: {text[:200]}")
        refund_detected = True
        refund_event.set()

# ============================================================
# HANDLER DEL TRIGGER
# ============================================================

_is_running = False

async def trigger_handler(event):
    print(f"   [trigger] Evento recibido de {event.sender_id} (ID del trigger: {TRIGGER_ID})")
    asyncio.create_task(trigger_flow())

async def trigger_flow():
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

# ============================================================
# ARRANQUE
# ============================================================

async def run_forever():
    global BOT_ID, TRIGGER_ID
    while True:
        try:
            if not SESSION_STRING:
                raise RuntimeError("TELEGRAM_SESSION no definida")
            await client.start()
            me = await client.get_me()
            if me is None:
                raise RuntimeError("Sesión no autorizada")

            if BOT_ID is None:
                try:
                    bot_entity = await client.get_entity(BOT)
                    BOT_ID = bot_entity.id
                    print(f">>> ID de {BOT} resuelto: {BOT_ID} <<<")
                except Exception as e:
                    print(f">>> No se pudo resolver ID de {BOT}: {e!r} <<<")

            if TRIGGER_ID is None:
                try:
                    trigger_entity = await client.get_entity(TRIGGER_USERNAME)
                    TRIGGER_ID = trigger_entity.id
                    print(f">>> ID de @{TRIGGER_USERNAME} resuelto: {TRIGGER_ID} <<<")
                except Exception as e:
                    print(f">>> No se pudo resolver ID del trigger: {e!r} <<<")

            client.remove_event_handler(trigger_handler, events.NewMessage)
            client.add_event_handler(trigger_handler, events.NewMessage(from_users=TRIGGER_ID))

            client.remove_event_handler(refund_handler, events.NewMessage)
            client.add_event_handler(refund_handler, events.NewMessage())

            print(">>> SERVICIO v8.5 ACTIVO (COL) - con reintentos y refund detector <<<")
            print(f">>> Logueado como: {me.first_name} (@{me.username}) <<<")
            print(f">>> Disparador: @{TRIGGER_USERNAME} (ID: {TRIGGER_ID}) <<<")
            print(f">>> Escuchando refunds de {BOT} (ID: {BOT_ID}) <<<")
            print(f">>> Precio máximo: ${MAX_PRICE} | Reintentos por acción: {MAX_RETRIES} <<<")

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
