import asyncio
import os
import time
import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ============================================================
# CONFIGURACIÓN GLOBAL
# ============================================================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")

SESSION_STRING = os.environ.get("TELEGRAM_SESSION", "").strip()

PRODUCTOS_FILE = "productos.txt"
MAX_PRICE = 6.0

TIMEOUT = 45
CLICK_TIMEOUT = 3
MAX_RETRIES = 2
RETRY_SLEEP = 1
HEADER_ATTEMPTS = 2
CHECK_ATTEMPTS = 2

# ─── Polling ───
POLL_INTERVAL = 0.5
POLL_INTERVAL_FAST = 0.2
MAX_PAGES = 300

# ─── View all information ───
VIEWALL_CAPTURE_TIMEOUT = 8
VIEWALL_SILENCE_THRESHOLD = 0.3       # silencio corto cuando YA llegó todo
VIEWALL_SILENCE_LONG = 2.0            # ← v9.9: silencio largo si FALTAN mensajes
VIEWALL_MIN_MESSAGES = 3              # mínimo para aplicar silencio corto
USE_VIEWALL = os.environ.get("USE_VIEWALL", "1").strip() not in ("0", "false", "False", "no")

# ─── Log verbose (imprime rechazos y todos los items) ───
VERBOSE_PAGES = os.environ.get("VERBOSE_PAGES", "1").strip() == "1"

# ─── Disparo cruzado ───
CROSS_TRIGGER_COOLDOWN = 60
_last_cross_fire = 0

DEBUG_ALL_MESSAGES = os.environ.get("DEBUG_ALL_MESSAGES", "0").strip() == "1"

# ─── Switch OLD ───
ENABLE_OLD = os.environ.get("ENABLE_OLD", "1").strip() not in ("0", "false", "False", "no")

MANUAL_WORD_BOTH = "run_test"
MANUAL_WORD_OLD = "run_test_old"
MANUAL_WORD_NEW = "run_test_new"

# ─── Whitelist ───
TRIGGER_WHITELIST = ["news cc", "new bases"]

if not os.path.exists(PRODUCTOS_FILE):
    with open(PRODUCTOS_FILE, "w", encoding="utf-8") as f:
        f.write(os.environ.get("PRODUCTOS_CONTENT", ""))

client = TelegramClient(
    StringSession(SESSION_STRING) if SESSION_STRING else "telegram_session",
    API_ID,
    API_HASH
)

INSUFFICIENT_MSG = "Current user's account balance is insufficient. Please return to the homepage to recharge or adjust the amount."
CARD_HEADER_FAIL_MSG = "If you fail to obtain the card header information, please check the card head again"
NO_STOCK_MSG = "If you fail to obtain the card secret list"


def _ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg):
    print(f"[{_ts()}] {msg}")


def fmt_secs(s):
    if s < 60:
        return f"{s:.2f}s"
    mins = int(s // 60)
    return f"{mins}m{s - mins * 60:.1f}s"


def matches_whitelist(text):
    if not text:
        return False
    t = text.lower()
    return any(kw in t for kw in TRIGGER_WHITELIST)


# ============================================================
# CLASE BOT WORKER
# ============================================================
class BotWorker:
    def __init__(self, name, bot_username, trigger_username,
                 trigger_whitelist=None, trigger_username_2=None,
                 trigger_whitelist_2=None):
        self.name = name
        self.bot_username = bot_username
        self.bot_id = None

        self.trigger_username = trigger_username
        self.trigger_id = None
        self.trigger_whitelist = trigger_whitelist

        self.trigger_username_2 = trigger_username_2
        self.trigger_id_2 = None
        self.trigger_whitelist_2 = trigger_whitelist_2 or []

        self.is_running = False
        self.last_flow_start = 0
        self.used_buttons = set()
        self.refund_detected = False
        self.refund_event = asyncio.Event()
        self.metrics = {}
        self.page_cache = {}

    def wlog(self, msg):
        log(f"[{self.name}] {msg}")

    def reset_metrics(self, trigger_name):
        self.metrics = {
            "run_start": time.monotonic(), "run_end": None,
            "trigger_name": trigger_name,
            "pages_visited": 0, "pages_skipped_cache": 0,
            "items_analyzed": 0, "items_valid": 0,
            "purchases_ok": 0, "purchases_order_failed": 0,
            "purchases_insufficient": 0, "purchases_header_fail": 0,
            "purchases_no_response": 0, "purchases_check_missing": 0,
            "clicks_total": 0, "clicks_timeout": 0, "clicks_error": 0,
            "clicks_success_first_try": 0, "clicks_success_after_retry": 0,
            "header_card_retries": 0, "header_check_retries": 0,
            "refunds_detected": 0, "no_stock_exits": 0,
            "viewall_used": 0, "viewall_messages": 0, "viewall_matched": 0,
            "viewall_incomplete": 0,  # ← v9.9: cuántas veces llegaron < expected
            "viewall_capture_time": [],
            "response_times": [], "page_times": [], "purchase_times": [],
            "phase_times": {},
        }

    def print_run_summary(self):
        m = self.metrics
        if not m or m.get("run_start") is None:
            return
        end = m["run_end"] if m["run_end"] is not None else time.monotonic()
        total = end - m["run_start"]

        print("\n" + "╔" + "═" * 70 + "╗")
        title = f"RESUMEN [{self.name}] @{m['trigger_name']}"
        print("║" + title.center(70) + "║")
        print("╚" + "═" * 70 + "╝")
        print(f"  Duración total:        {fmt_secs(total)}")
        print(f"  Páginas visitadas:     {m['pages_visited']}")
        print(f"  Páginas saltadas cache: {m['pages_skipped_cache']}")
        print(f"  Artículos analizados:  {m['items_analyzed']}")
        print(f"  Artículos válidos:     {m['items_valid']}")
        print()
        print(f"  ✅ Compras OK:         {m['purchases_ok']}")
        print(f"  ✗ Order failed:        {m['purchases_order_failed']}")
        print(f"  ✗ Error cabecera:      {m['purchases_header_fail']}")
        print(f"  ✗ Sin respuesta:       {m['purchases_no_response']}")
        print(f"  ✗ Check faltante:      {m['purchases_check_missing']}")
        print(f"  ✗ Saldo insuficiente:  {m['purchases_insufficient']}")
        print(f"  💰 Refunds:            {m['refunds_detected']}")
        print(f"  📭 Sin stock (exits):  {m['no_stock_exits']}")
        print()
        print(f"  🔥 View-all usado:     {m['viewall_used']}")
        print(f"     - Mensajes capturados: {m['viewall_messages']}")
        print(f"     - Tarjetas matcheadas: {m['viewall_matched']}")
        print(f"     - Capturas incompletas: {m['viewall_incomplete']}")
        if m["viewall_capture_time"]:
            avg_capture = sum(m["viewall_capture_time"]) / len(m["viewall_capture_time"])
            print(f"     - Tiempo captura (prom): {avg_capture:.3f}s")
        print()
        print(f"  Clics totales:         {m['clicks_total']}")
        print(f"    - Éxito 1er intento: {m['clicks_success_first_try']}")
        print(f"    - Éxito tras retry:  {m['clicks_success_after_retry']}")
        print(f"    - Timeouts de clic:  {m['clicks_timeout']}")
        print(f"    - Errores de clic:   {m['clicks_error']}")
        print(f"  Reintentos cabecera tarjeta: {m['header_card_retries']}")
        print(f"  Reintentos cabecera check:   {m['header_check_retries']}")
        print()

        if m["response_times"]:
            rts = [t for _, t in m["response_times"]]
            print(f"  Tiempos de respuesta:")
            print(f"    - Muestras:          {len(rts)}")
            print(f"    - Promedio:          {sum(rts)/len(rts):.2f}s")
            print(f"    - Mínimo:            {min(rts):.2f}s")
            print(f"    - Máximo:            {max(rts):.2f}s")
            print()

        if m["purchase_times"]:
            print(f"  Compras individuales:")
            for item_id, secs, result in m["purchase_times"]:
                symbol = "✅" if result == "ok" else "✗"
                print(f"    {symbol} {item_id:<20} {secs:>7.2f}s  [{result}]")
            print()

        if m["page_times"]:
            print(f"  Tiempos por página:")
            for page_num, secs in m["page_times"]:
                print(f"    Página {page_num:<3} {secs:>7.2f}s")
            print()

        print("=" * 72 + "\n")

    def _snapshot(self, msg):
        btns = []
        if msg.buttons:
            for row in msg.buttons:
                for b in row:
                    btns.append(b.text)
        return (msg.text or "", tuple(btns))

    async def get_baseline(self):
        messages = await client.get_messages(self.bot_username, limit=3)
        for m in messages:
            if not m.out:
                return m.id, self._snapshot(m)
        return 0, ("", tuple())

    async def wait_for_response(self, baseline_id, baseline_sig, timeout=TIMEOUT, attempt=1):
        t0 = time.monotonic()
        deadline = t0 + timeout
        poll_count = 0
        while time.monotonic() < deadline:
            poll_count += 1
            try:
                messages = await client.get_messages(self.bot_username, limit=3)
            except Exception as e:
                self.wlog(f"   [poll] Error: {e}")
                await asyncio.sleep(1)
                continue
            for m in messages:
                if m.out:
                    continue
                if m.id > baseline_id:
                    elapsed = time.monotonic() - t0
                    self.wlog(f"   [poll] Nuevo mensaje id={m.id} (espera={elapsed:.2f}s, polls={poll_count})")
                    self.metrics["response_times"].append(("new_msg", elapsed))
                    return m
                if m.id == baseline_id and baseline_sig is not None:
                    sig = self._snapshot(m)
                    if sig != baseline_sig:
                        elapsed = time.monotonic() - t0
                        self.wlog(f"   [poll] Mensaje editado id={m.id} (espera={elapsed:.2f}s, polls={poll_count})")
                        self.metrics["response_times"].append(("edited", elapsed))
                        return m
            await asyncio.sleep(POLL_INTERVAL)
        self.wlog(f"   [poll] ⏱ TIMEOUT tras {timeout}s (polls={poll_count})")
        return None

    # ========================================================
    # v9.9: captura múltiple con silence threshold inteligente
    # ========================================================
    async def wait_for_multiple_responses(self, baseline_id, expected_count, timeout=VIEWALL_CAPTURE_TIMEOUT):
        """
        Captura múltiples mensajes del View All.
        Reglas de salida:
        - Si llegan >= expected_count → salir rápido (silencio corto 0.3s)
        - Si hay silencio > 0.3s Y ya tenemos >= expected_count → salir
        - Si hay silencio > 2.0s Y faltan mensajes (>= MIN_MESSAGES) → salir (raro, pero esperar más)
        - Si timeout total (8s) → salir con lo que haya
        """
        t0 = time.monotonic()
        deadline = t0 + timeout
        captured = []
        seen_ids = set()
        last_msg_time = None

        while time.monotonic() < deadline:
            try:
                messages = await client.get_messages(self.bot_username, limit=30)
            except Exception as e:
                self.wlog(f"   [multi] Error poll: {e}")
                await asyncio.sleep(POLL_INTERVAL_FAST)
                continue

            new_in_this_poll = 0
            for m in messages:
                if m.out:
                    continue
                if m.id <= baseline_id:
                    continue
                if m.id in seen_ids:
                    continue
                captured.append(m)
                seen_ids.add(m.id)
                new_in_this_poll += 1

            if new_in_this_poll > 0:
                last_msg_time = time.monotonic()
                self.wlog(f"   [multi] +{new_in_this_poll} mensajes (total={len(captured)}/{expected_count})")

            # Objetivo alcanzado
            if len(captured) >= expected_count:
                self.wlog(f"   [multi] ✅ Objetivo alcanzado ({len(captured)}/{expected_count})")
                break

            # Silence check (v9.9: inteligente según cuántos faltan)
            if last_msg_time is not None:
                silence = time.monotonic() - last_msg_time
                if len(captured) >= expected_count and silence > VIEWALL_SILENCE_THRESHOLD:
                    self.wlog(f"   [multi] Silencio corto {silence:.2f}s (completo)")
                    break
                elif len(captured) >= VIEWALL_MIN_MESSAGES and silence > VIEWALL_SILENCE_LONG:
                    self.wlog(f"   [multi] ⚠️ Silencio largo {silence:.2f}s tras {len(captured)}/{expected_count} (incompleto)")
                    self.metrics["viewall_incomplete"] += 1
                    break

            await asyncio.sleep(POLL_INTERVAL_FAST)

        elapsed = time.monotonic() - t0
        captured.sort(key=lambda m: m.id)
        status = "✅ completo" if len(captured) >= expected_count else f"⚠️ incompleto ({len(captured)}/{expected_count})"
        self.wlog(f"   [multi] Total: {len(captured)}/{expected_count} mensajes en {elapsed:.3f}s — {status}")
        self.metrics["viewall_capture_time"].append(elapsed)
        return captured

    async def click_and_wait_with_retry(self, message, text, timeout=TIMEOUT, max_retries=MAX_RETRIES):
        self.metrics["clicks_total"] += 1
        for attempt in range(1, max_retries + 1):
            baseline_id, baseline_sig = await self.get_baseline()
            self.wlog(f"   [click] Intento {attempt}/{max_retries} para '{text[:50]}...'")
            click_task = asyncio.create_task(message.click(text=text))
            try:
                await asyncio.wait_for(click_task, timeout=CLICK_TIMEOUT)
            except asyncio.TimeoutError:
                self.metrics["clicks_timeout"] += 1
                self.wlog(f"   [click] ⏱ Timeout de clic (>{CLICK_TIMEOUT}s, intento {attempt})")
            except Exception as e:
                self.metrics["clicks_error"] += 1
                self.wlog(f"   [click] ✗ Error: {e!r} (intento {attempt})")
            response = await self.wait_for_response(baseline_id, baseline_sig, timeout, attempt)
            if response is not None:
                if attempt == 1:
                    self.metrics["clicks_success_first_try"] += 1
                else:
                    self.metrics["clicks_success_after_retry"] += 1
                return response
            if attempt < max_retries:
                self.wlog(f"   [reintento] Esperando {RETRY_SLEEP}s...")
                await asyncio.sleep(RETRY_SLEEP)
        self.wlog(f"   [click] ✗ Fallaron todos los intentos.")
        return None

    async def send_and_wait(self, text, timeout=TIMEOUT):
        baseline_id, baseline_sig = await self.get_baseline()
        await client.send_message(self.bot_username, text)
        return await self.wait_for_response(baseline_id, baseline_sig, timeout)

    def load_products(self):
        products = []
        self.wlog("Cargando productos.txt...")
        with open(PRODUCTOS_FILE, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, 1):
                product_id = line.strip()
                if not product_id:
                    continue
                products.append({"id": product_id, "priority": line_number})
        self.wlog(f"[DEBUG] IDs cargados: {len(products)}")
        return products

    def print_message(self, message):
        self.wlog("=" * 60)
        self.wlog(f"ID: {message.id}")
        self.wlog("TEXTO:")
        self.wlog(message.text or "(sin texto)")
        if message.buttons:
            self.wlog("BOTONES:")
            for r_i, row in enumerate(message.buttons):
                for c_i, button in enumerate(row):
                    self.wlog(f"[{r_i},{c_i}] {button.text}")
        self.wlog("=" * 60)

    def get_items(self, message):
        items = []
        if not message.buttons:
            return items
        for row in message.buttons:
            for button in row:
                if "|" in button.text:
                    items.append(button.text)
        return items

    def find_button(self, message, text):
        if not message.buttons:
            return None
        for row in message.buttons:
            for button in row:
                if button.text.strip().lower() == text.strip().lower():
                    return button
        return None

    def find_check_button(self, message):
        if not message.buttons:
            return None
        for row in message.buttons:
            for button in row:
                if "check" in button.text.lower():
                    return button
        return None

    def extract_id(self, item_text):
        parts = item_text.split("|")
        return parts[0].strip() if len(parts) >= 2 else None

    def extract_price(self, item_text):
        parts = item_text.split("|")
        if len(parts) < 2:
            return None
        p = parts[1].strip().replace("💵", "").replace("$", "").replace("USD", "").strip()
        try:
            return float(p.replace(",", "."))
        except ValueError:
            return None

    def extract_base(self, item_text):
        parts = item_text.split("|")
        return parts[2].strip() if len(parts) >= 3 else None

    def parse_detail_message(self, msg):
        text = msg.text or ""
        bin_val = None
        base_val = None
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("Bin:"):
                bin_val = line.split(":", 1)[1].strip()
            elif line.startswith("Base:"):
                base_val = line.split(":", 1)[1].strip()

        check_btn = self.find_check_button(msg)
        check_price = None
        if check_btn:
            try:
                price_str = check_btn.text.lower().replace("check", "").replace("$", "").strip()
                check_price = float(price_str)
            except Exception:
                pass

        return {
            "message": msg,
            "bin": bin_val,
            "base": base_val,
            "check_btn": check_btn,
            "check_price": check_price,
            "text": text,
        }

    def filter_page_items(self, items, products, page_num):
        product_ids = {p["id"]: p["priority"] for p in products}
        valid = []
        rejected = []

        if VERBOSE_PAGES:
            self.wlog(f"   [debug] Analizando {len(items)} artículos de la página {page_num}...")

        for item in items:
            self.metrics["items_analyzed"] += 1
            item_id = self.extract_id(item)
            price = self.extract_price(item)
            if item_id is None or price is None:
                rejected.append(f"ilegible: {item!r}")
                continue
            if item_id not in product_ids:
                rejected.append(f"{item_id} | ✗ NO en productos.txt")
                continue
            if price > MAX_PRICE:
                rejected.append(f"{item_id} | ${price:.2f} | ✗ precio > {MAX_PRICE}")
                continue
            self.wlog(f"   [debug] Pág {page_num} | {item_id} | ${price:.2f} | ✓ VÁLIDO")
            self.metrics["items_valid"] += 1
            valid.append({
                "id": item_id, "item": item, "price": price,
                "priority": product_ids[item_id], "page": page_num,
            })

        if VERBOSE_PAGES and rejected:
            for r in rejected:
                self.wlog(f"   [debug] Pág {page_num} | {r}")

        seen = set()
        uniq = []
        for rec in sorted(valid, key=lambda x: x["price"]):
            if rec["item"] not in seen:
                seen.add(rec["item"])
                uniq.append(rec)
        uniq.sort(key=lambda x: (x["priority"], x["price"]))
        return uniq

    async def navigate_to_page(self, current_page, target_page, message):
        while current_page < target_page:
            next_btn = self.find_button(message, "next page ➡️")
            if not next_btn:
                self.wlog("No se encontró botón next page")
                return None
            new_msg = await self.click_and_wait_with_retry(message, next_btn.text)
            if not new_msg:
                return None
            message = new_msg
            current_page += 1
        while current_page > target_page:
            prev_btn = self.find_button(message, "Previous")
            if not prev_btn:
                self.wlog("No se encontró botón Previous")
                return None
            new_msg = await self.click_and_wait_with_retry(message, prev_btn.text)
            if not new_msg:
                return None
            message = new_msg
            current_page -= 1
        return message

    async def purchase_page_via_viewall(self, purchase_list, current_page, list_message):
        total_items_in_list = len(self.get_items(list_message))
        self.wlog(f"\n>>> [VIEWALL] Página {current_page}: {len(purchase_list)} válidas de {total_items_in_list} totales")

        viewall_btn = self.find_button(list_message, "View all information")
        if not viewall_btn:
            self.wlog("   ✗ No se encontró botón 'View all information'. Fallback a flujo normal.")
            return None

        baseline_id, _ = await self.get_baseline()

        self.wlog(f"   [viewall] Clic en 'View all information' (esperando ~{total_items_in_list} mensajes)...")
        click_task = asyncio.create_task(list_message.click(text=viewall_btn.text))
        try:
            await asyncio.wait_for(click_task, timeout=CLICK_TIMEOUT)
        except asyncio.TimeoutError:
            self.metrics["clicks_timeout"] += 1
            self.wlog(f"   [viewall] ⏱ Timeout de clic")
        except Exception as e:
            self.metrics["clicks_error"] += 1
            self.wlog(f"   [viewall] ✗ Error de clic: {e!r}")

        details = await self.wait_for_multiple_responses(
            baseline_id,
            expected_count=total_items_in_list,
            timeout=VIEWALL_CAPTURE_TIMEOUT,
        )
        self.metrics["viewall_used"] += 1
        self.metrics["viewall_messages"] += len(details)

        if not details:
            self.wlog("   ✗ No se capturaron mensajes de detalle")
            return 0

        parsed = [self.parse_detail_message(m) for m in details]
        self.wlog(f"   [viewall] Parseados {len(parsed)} detalles")

        matches = []
        parsed_used = set()

        for rec in purchase_list:
            rec_id = rec["id"]
            rec_base = self.extract_base(rec["item"])

            for idx, p in enumerate(parsed):
                if idx in parsed_used:
                    continue
                if p["bin"] == rec_id and p["base"] == rec_base:
                    matches.append((rec, p))
                    parsed_used.add(idx)
                    break
            else:
                self.wlog(f"   [viewall] ⚠️ No se encontró detalle para {rec_id} | {rec_base}")

        self.metrics["viewall_matched"] += len(matches)
        self.wlog(f"   [viewall] Matched: {len(matches)}/{len(purchase_list)}")

        if not matches:
            self.wlog("   ✗ Ningún match. Fin de página.")
            return 0

        total_ok = 0
        for rec, p in matches:
            item_start = time.monotonic()

            if not p["check_btn"]:
                self.wlog(f"   ✗ {rec['id']} sin botón check")
                self.metrics["purchases_check_missing"] += 1
                self.metrics["purchase_times"].append((rec["id"], time.monotonic() - item_start, "check_missing"))
                continue

            self.wlog(f"\n>>> [CHECK] {rec['id']} | ${p['check_price']} | {p['base']}")

            final = await self.click_and_wait_with_retry(p["message"], p["check_btn"].text)

            if final is None:
                self.wlog(f"   ✗ Sin respuesta al check")
                self.metrics["purchases_no_response"] += 1
                self.metrics["purchase_times"].append((rec["id"], time.monotonic() - item_start, "no_response"))
                continue

            final_text = final.text or ""

            if CARD_HEADER_FAIL_MSG in final_text:
                self.wlog(f"   ⚠️ Error cabecera en check")
                self.metrics["purchases_header_fail"] += 1
                self.metrics["purchase_times"].append((rec["id"], time.monotonic() - item_start, "header_fail"))
                continue

            if INSUFFICIENT_MSG in final_text:
                self.wlog(f"   ✗ Saldo insuficiente. Deteniendo página.")
                self.metrics["purchases_insufficient"] += 1
                self.metrics["purchase_times"].append((rec["id"], time.monotonic() - item_start, "insufficient"))
                return total_ok

            if "Order failed" in final_text:
                self.wlog(f"   ✗ Order failed")
                self.metrics["purchases_order_failed"] += 1
                self.metrics["purchase_times"].append((rec["id"], time.monotonic() - item_start, "order_failed"))
                continue

            self.wlog(f"   ✅ COMPRA CONFIRMADA:")
            self.print_message(final)
            self.metrics["purchases_ok"] += 1
            self.metrics["purchase_times"].append((rec["id"], time.monotonic() - item_start, "ok"))
            total_ok += 1

        return total_ok

    async def purchase_item(self, record, current_page, message):
        item_start = time.monotonic()
        self.wlog(f"\n>>> Comprando (normal): {record['item']} (pág {record['page']}, prioridad {record['priority']})")

        if current_page != record["page"]:
            message = await self.navigate_to_page(current_page, record["page"], message)
            if not message:
                self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "nav_fail"))
                return True, current_page, message
            current_page = record["page"]

        if not message.buttons:
            self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "no_buttons"))
            return True, current_page, message

        found = any(button.text.strip() == record["item"].strip()
                    for row in message.buttons for button in row)
        if not found:
            self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "button_gone"))
            return True, current_page, message

        for card_attempt in range(1, HEADER_ATTEMPTS + 1):
            if card_attempt > 1:
                self.metrics["header_card_retries"] += 1
                self.wlog(f"   🔄 [TARJETA] Reintento {card_attempt - 1}/{HEADER_ATTEMPTS - 1}...")
                if not message.buttons:
                    self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "no_buttons_retry"))
                    return True, current_page, message
                found = any(button.text.strip() == record["item"].strip()
                            for row in message.buttons for button in row)
                if not found:
                    self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "button_gone_retry"))
                    return True, current_page, message

            response = await self.click_and_wait_with_retry(message, record["item"])
            if response is None:
                self.metrics["purchases_no_response"] += 1
                self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "no_response"))
                return True, current_page, message

            self.used_buttons.add(record["item"])

            if response.text and INSUFFICIENT_MSG in response.text:
                self.metrics["purchases_insufficient"] += 1
                self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "insufficient"))
                return True, current_page, message

            if response.text and CARD_HEADER_FAIL_MSG in response.text:
                self.wlog("   ⚠️ Error cabecera tras TARJETA.")
                await asyncio.sleep(1)
                continue

            self.wlog("   Respuesta del bot tras clic en tarjeta:")
            self.print_message(response)

            check_btn = self.find_check_button(response)
            if not check_btn:
                self.metrics["purchases_check_missing"] += 1
                self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "check_missing"))
                return True, current_page, message

            self.wlog(f"   -> [CHECK] {CHECK_ATTEMPTS} intento(s) disponible(s)")
            for check_attempt in range(1, CHECK_ATTEMPTS + 1):
                if check_attempt > 1:
                    self.metrics["header_check_retries"] += 1
                    self.wlog(f"   🔄 [CHECK] Reintento {check_attempt - 1}/{CHECK_ATTEMPTS - 1}...")
                    check_btn = self.find_check_button(response)
                    if not check_btn:
                        break

                final = await self.click_and_wait_with_retry(response, check_btn.text)
                if final is None:
                    continue

                final_text = final.text or ""

                if CARD_HEADER_FAIL_MSG in final_text:
                    self.wlog("   ⚠️ Error cabecera tras CHECK.")
                    await asyncio.sleep(1)
                    continue

                if INSUFFICIENT_MSG in final_text:
                    self.metrics["purchases_insufficient"] += 1
                    self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "insufficient_check"))
                    return True, current_page, message

                if "Order failed" in final_text:
                    self.metrics["purchases_order_failed"] += 1
                    self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "order_failed"))
                    return True, current_page, message

                self.wlog("   ✅ COMPRA CONFIRMADA:")
                self.print_message(final)
                self.metrics["purchases_ok"] += 1
                self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "ok"))
                return True, current_page, message

            if card_attempt < HEADER_ATTEMPTS:
                self.wlog(f"   ✗ Check agotó intentos. Reintentando tarjeta...")
                await asyncio.sleep(1)
                continue
            else:
                self.metrics["purchases_header_fail"] += 1
                self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "check_fail_exhausted"))
                return True, current_page, message

        self.metrics["purchases_header_fail"] += 1
        self.metrics["purchase_times"].append((record["id"], time.monotonic() - item_start, "header_fail_exhausted"))
        return True, current_page, message

    async def start_flow(self, max_retries=3):
        for attempt in range(1, max_retries + 1):
            self.wlog(f"=== Intento {attempt}/{max_retries} ===")

            self.wlog("[1] Enviando 'Country'...")
            message = await self.send_and_wait("Country")
            if not message:
                self.wlog("No hubo respuesta a 'Country'")
                await asyncio.sleep(2)
                continue

            self.wlog("[2] Enviando 'CO'...")
            message = await self.send_and_wait("CO")
            if not message:
                self.wlog("No hubo respuesta a 'CO'")
                await asyncio.sleep(2)
                continue

            self.wlog("✅ Listo en pantalla de tarjetas")
            return message
        return None

    async def run(self, trigger_name):
        self.page_cache.clear()
        self.reset_metrics(trigger_name)
        self.wlog(f">>> INICIANDO FLUJO (trigger={trigger_name}) <<<")

        while True:
            self.used_buttons.clear()
            self.refund_detected = False

            products = self.load_products()
            if not products:
                self.wlog("⚠️ Sin productos")
                self.metrics["run_end"] = time.monotonic()
                self.print_run_summary()
                return

            message = await self.start_flow(max_retries=3)
            if not message:
                self.wlog("No se completó flujo inicial")
                self.metrics["run_end"] = time.monotonic()
                self.print_run_summary()
                return
            self.print_message(message)

            if message.text and NO_STOCK_MSG in message.text:
                self.wlog("📭 No hay stock (card secret list). Saliendo del flujo sin esperar refunds.")
                self.metrics["no_stock_exits"] += 1
                self.metrics["run_end"] = time.monotonic()
                self.print_run_summary()
                return

            current_page = 1
            total = 0

            while True:
                page_t0 = time.monotonic()
                self.metrics["pages_visited"] += 1
                items = self.get_items(message)
                items_tuple = tuple(items)

                cached_items = self.page_cache.get(current_page)
                if cached_items is not None and cached_items == items_tuple:
                    self.metrics["pages_skipped_cache"] += 1
                    self.wlog(f"===== PÁGINA {current_page} ===== (⏩ CACHE: {len(items)} items idénticos)")
                    self.metrics["page_times"].append((current_page, time.monotonic() - page_t0))

                    next_btn = self.find_button(message, "next page ➡️")
                    if not next_btn:
                        self.wlog("Fin del recorrido")
                        break
                    new_msg = await self.click_and_wait_with_retry(message, next_btn.text)
                    if not new_msg:
                        break
                    message = new_msg
                    current_page += 1
                    if current_page > MAX_PAGES:
                        break
                    continue

                self.wlog(f"===== PÁGINA {current_page} =====")
                self.wlog(f"Artículos: {len(items)}")

                purchase_list = self.filter_page_items(items, products, current_page) if items else []

                if not purchase_list:
                    self.page_cache[current_page] = items_tuple

                if purchase_list:
                    self.wlog(f"   → {len(purchase_list)} válidas en esta página:")
                    for idx, rec in enumerate(purchase_list, 1):
                        self.wlog(f"      {idx}. ID {rec['id']} | ${rec['price']:.2f} | P{rec['priority']}")

                    if USE_VIEWALL and len(items) >= 2:
                        self.wlog(f"🔥 [VIEWALL] Usando flujo rápido para {len(purchase_list)} válidas")
                        ok_count = await self.purchase_page_via_viewall(purchase_list, current_page, message)
                        if ok_count is not None:
                            total += ok_count
                            self.metrics["page_times"].append((current_page, time.monotonic() - page_t0))
                            next_btn = self.find_button(message, "next page ➡️")
                            if not next_btn:
                                self.wlog("Fin del recorrido")
                                break
                            self.wlog("Pasando a la siguiente página...")
                            new_msg = await self.click_and_wait_with_retry(message, next_btn.text)
                            if not new_msg:
                                break
                            message = new_msg
                            current_page += 1
                            if current_page > MAX_PAGES:
                                break
                            continue
                        else:
                            self.wlog("   Fallback a flujo normal")

                    for rec in purchase_list:
                        success, current_page, message = await self.purchase_item(rec, current_page, message)
                        total += 1
                else:
                    self.wlog(f"   ✗ 0 válidas (cacheada para próximos ciclos)")

                self.metrics["page_times"].append((current_page, time.monotonic() - page_t0))

                next_btn = self.find_button(message, "next page ➡️")
                if not next_btn:
                    self.wlog("Fin del recorrido")
                    break

                self.wlog("Pasando a la siguiente página...")
                new_msg = await self.click_and_wait_with_retry(message, next_btn.text)
                if not new_msg:
                    break
                message = new_msg
                current_page += 1
                if current_page > MAX_PAGES:
                    break

            self.wlog(f"Recorrido completo - {total} compras intentadas")

            if self.refund_detected:
                self.wlog("✅ Refund detectado. Reiniciando (cache preservada)...")
                continue

            self.wlog("⏳ Esperando hasta 2 min por refunds...")
            try:
                await asyncio.wait_for(self.refund_event.wait(), timeout=120)
                self.wlog("✅ Refund detectado. Reiniciando (cache preservada)...")
                self.refund_event.clear()
                continue
            except asyncio.TimeoutError:
                self.wlog("⏰ Sin refunds. Fin.")
                break

        self.wlog(">>> Flujo finalizado <<<")
        self.metrics["run_end"] = time.monotonic()
        self.print_run_summary()

    async def trigger_flow(self, trigger_name):
        if self.is_running:
            self.wlog(f">>> Ya hay una ejecución en curso. Ignorando trigger '{trigger_name}'. <<<")
            return
        self.is_running = True
        self.last_flow_start = time.monotonic()
        try:
            self.wlog(f">>> TRIGGER de {trigger_name} <<<")
            await self.run(trigger_name)
        except Exception as e:
            self.wlog(f">>> ERROR: {e!r} <<<")
        finally:
            self.is_running = False
            self.wlog(">>> Listo para próximo trigger <<<")

    async def resolve_ids(self):
        try:
            e = await client.get_entity(self.bot_username)
            self.bot_id = e.id
            log(f">>> [{self.name}] Bot {self.bot_username} → ID {self.bot_id}")
        except Exception as e:
            log(f">>> [{self.name}] No se pudo resolver bot: {e!r}")

        try:
            e = await client.get_entity(self.trigger_username)
            self.trigger_id = e.id
            log(f">>> [{self.name}] Trigger 1 {self.trigger_username} → ID {self.trigger_id}")
        except Exception as e:
            log(f">>> [{self.name}] No se pudo resolver trigger 1: {e!r}")

        if self.trigger_username_2:
            try:
                e = await client.get_entity(self.trigger_username_2)
                self.trigger_id_2 = e.id
                log(f">>> [{self.name}] Trigger 2 {self.trigger_username_2} → ID {self.trigger_id_2}")
            except Exception as e:
                log(f">>> [{self.name}] No se pudo resolver trigger 2: {e!r}")


# ============================================================
# INSTANCIAS
# ============================================================
worker_old = BotWorker(
    name="OLD",
    bot_username="@Globalccvs_Bot",
    trigger_username="ccscards_bot",
    trigger_whitelist=TRIGGER_WHITELIST,
    trigger_username_2="globalccvs_bot",
    trigger_whitelist_2=TRIGGER_WHITELIST,
)

worker_new = BotWorker(
    name="NEW",
    bot_username="@KingKongccs2bot",
    trigger_username="kingkongccs2bot",
    trigger_whitelist=TRIGGER_WHITELIST,
    trigger_username_2=None,
    trigger_whitelist_2=[],
)


# ============================================================
# HELPERS
# ============================================================
def text_matches_whitelist(text, whitelist):
    if whitelist is None:
        return True
    if not whitelist:
        return False
    t = (text or "").lower()
    return any(kw in t for kw in whitelist)


async def fire_both(source, is_manual=False):
    global _last_cross_fire

    if not is_manual:
        elapsed = time.monotonic() - _last_cross_fire
        if elapsed < CROSS_TRIGGER_COOLDOWN:
            log(f"   [cross] Trigger {source} ignorado (cooldown {elapsed:.1f}s < {CROSS_TRIGGER_COOLDOWN}s)")
            return
        _last_cross_fire = time.monotonic()

    old_busy = ENABLE_OLD and worker_old.is_running
    new_busy = worker_new.is_running

    if old_busy and new_busy:
        log(f"   [cross] Trigger {source} ignorado (ambos workers ya corriendo)")
        return

    if ENABLE_OLD:
        log(f"   [cross] Trigger {source} → disparando OLD + NEW")
        if not old_busy:
            asyncio.create_task(worker_old.trigger_flow(f"CROSS-OLD[{source}]"))
        if not new_busy:
            asyncio.create_task(worker_new.trigger_flow(f"CROSS-NEW[{source}]"))
    else:
        log(f"   [cross] Trigger {source} → solo NEW (OLD desactivado)")
        if not new_busy:
            asyncio.create_task(worker_new.trigger_flow(f"CROSS-NEW[{source}]"))


# ============================================================
# HANDLERS
# ============================================================

async def old_trigger1_handler(event):
    if event.message.out:
        return
    text = event.message.text or ""
    if not text_matches_whitelist(text, worker_old.trigger_whitelist):
        log(f"   [OLD-t1] IGNORADO (no NEWS CC): {text[:80]!r}")
        return
    log(f"   [OLD-t1] ✅ TRIGGER VÁLIDO: {text[:80]!r}")
    await fire_both("OLD-t1")


async def old_trigger2_handler(event):
    if event.message.out:
        return
    text = event.message.text or ""
    if not text_matches_whitelist(text, worker_old.trigger_whitelist_2):
        log(f"   [OLD-t2] IGNORADO (no NEWS CC): {text[:80]!r}")
        return
    log(f"   [OLD-t2] ✅ TRIGGER VÁLIDO: {text[:80]!r}")
    await fire_both("OLD-t2")


async def new_trigger_handler(event):
    if event.message.out:
        return
    text = event.message.text or ""
    if not text_matches_whitelist(text, worker_new.trigger_whitelist):
        log(f"   [NEW-t1] IGNORADO (no NEWS CC): {text[:80]!r}")
        return
    log(f"   [NEW-t1] ✅ TRIGGER VÁLIDO: {text[:80]!r}")
    await fire_both("NEW-t1")


async def refund_handler(event):
    if event.message.out:
        return
    text = event.message.text or ""
    if not ("refund" in text.lower() and "account balance" in text.lower()):
        return

    sender_id = event.sender_id
    if ENABLE_OLD and worker_old.bot_id is not None and sender_id == worker_old.bot_id:
        worker_old.metrics["refunds_detected"] = worker_old.metrics.get("refunds_detected", 0) + 1
        log(f"\n💰 [OLD] REFUND: {text[:200]}")
        worker_old.refund_detected = True
        worker_old.refund_event.set()
    elif worker_new.bot_id is not None and sender_id == worker_new.bot_id:
        worker_new.metrics["refunds_detected"] = worker_new.metrics.get("refunds_detected", 0) + 1
        log(f"\n💰 [NEW] REFUND: {text[:200]}")
        worker_new.refund_detected = True
        worker_new.refund_event.set()


async def manual_trigger_handler(event):
    if not event.message.out:
        return
    text = (event.message.text or "").strip().lower()
    if not text:
        return
    log(f"   [diag-manual] chat_id={event.chat_id} texto={text[:60]!r}")

    if MANUAL_WORD_OLD in text:
        if not ENABLE_OLD:
            log(f"   [manual] {MANUAL_WORD_OLD} ignorado (OLD desactivado)")
            return
        log(f"   [manual] {MANUAL_WORD_OLD} → worker OLD")
        asyncio.create_task(worker_old.trigger_flow("MANUAL_OLD"))
    elif MANUAL_WORD_NEW in text:
        log(f"   [manual] {MANUAL_WORD_NEW} → worker NEW")
        asyncio.create_task(worker_new.trigger_flow("MANUAL_NEW"))
    elif MANUAL_WORD_BOTH in text:
        log(f"   [manual] {MANUAL_WORD_BOTH} → disparo cruzado manual")
        asyncio.create_task(fire_both("MANUAL-BOTH", is_manual=True))


async def debug_all_handler(event):
    if not DEBUG_ALL_MESSAGES:
        return
    sender = event.sender_id
    text = (event.message.text or "")[:80]
    log(f"   [DEBUG-ALL] sender={sender} out={event.message.out} text={text!r}")


# ============================================================
# ARRANQUE
# ============================================================
async def run_forever():
    while True:
        try:
            if not SESSION_STRING:
                raise RuntimeError("TELEGRAM_SESSION no definida")
            await client.start()
            me = await client.get_me()
            if me is None:
                raise RuntimeError("Sesión no autorizada")

            if ENABLE_OLD:
                await worker_old.resolve_ids()
            await worker_new.resolve_ids()

            client.remove_event_handler(old_trigger1_handler, events.NewMessage)
            client.remove_event_handler(old_trigger2_handler, events.NewMessage)
            client.remove_event_handler(new_trigger_handler, events.NewMessage)
            client.remove_event_handler(refund_handler, events.NewMessage)
            client.remove_event_handler(manual_trigger_handler, events.NewMessage)
            client.remove_event_handler(debug_all_handler, events.NewMessage)

            if DEBUG_ALL_MESSAGES:
                client.add_event_handler(debug_all_handler, events.NewMessage())

            if ENABLE_OLD:
                if worker_old.trigger_id is not None:
                    client.add_event_handler(
                        old_trigger1_handler,
                        events.NewMessage(from_users=worker_old.trigger_id)
                    )
                if worker_old.trigger_id_2 is not None:
                    client.add_event_handler(
                        old_trigger2_handler,
                        events.NewMessage(from_users=worker_old.trigger_id_2)
                    )

            if worker_new.trigger_id is not None:
                client.add_event_handler(
                    new_trigger_handler,
                    events.NewMessage(from_users=worker_new.trigger_id)
                )

            client.add_event_handler(refund_handler, events.NewMessage())
            client.add_event_handler(manual_trigger_handler, events.NewMessage(outgoing=True))

            log(">>> SERVICIO v9.9 ACTIVO — SILENCE INTELIGENTE <<<")
            log(f">>> Logueado como: {me.first_name} (@{me.username}) <<<")

            if ENABLE_OLD:
                log(f">>> [OLD] ✅ ACTIVO | Bot: {worker_old.bot_username} <<<")
            else:
                log(f">>> [OLD] ⏸ DESACTIVADO (ENABLE_OLD=False) <<<")

            log(f">>> [NEW] ✅ ACTIVO | Bot: {worker_new.bot_username} <<<")
            log(f">>> Cooldown cruzado: {CROSS_TRIGGER_COOLDOWN}s <<<")
            log(f">>> USE_VIEWALL: {USE_VIEWALL} | Silence corto: {VIEWALL_SILENCE_THRESHOLD}s | Silence largo: {VIEWALL_SILENCE_LONG}s <<<")
            log(f">>> VERBOSE_PAGES: {VERBOSE_PAGES} | Cache de páginas: ACTIVADA <<<")
            log(f">>> Whitelist: {TRIGGER_WHITELIST} (case-insensitive) <<<")
            log(f">>> Manuales: '{MANUAL_WORD_BOTH}' (ambos), '{MANUAL_WORD_OLD}', '{MANUAL_WORD_NEW}' <<<")
            log(f">>> Precio máx: ${MAX_PRICE} | Tarjeta: {HEADER_ATTEMPTS} | Check: {CHECK_ATTEMPTS} | Clic: {MAX_RETRIES}x cada {RETRY_SLEEP}s <<<")

            await client.run_until_disconnected()

        except Exception as e:
            log(f">>> CONEXIÓN CAÍDA: {e!r} <<<")
            log(">>> Reintentando en 15 segundos... <<<")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(15)


log(">>> Iniciando servicio v9.9 — silence inteligente <<<")
client.loop.run_until_complete(run_forever())
