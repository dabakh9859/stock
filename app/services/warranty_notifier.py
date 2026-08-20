import os
import os
import threading
import time
from datetime import datetime, date, timedelta
import json
from urllib import request as _urlrequest
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from ..database import get_db, Invoice, Client, AppCache, SessionLocal


class WarrantyNotifier:
    """Service de notification automatique pour les garanties qui arrivent à expiration."""
    
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_seconds = int(os.getenv("WARRANTY_REMINDER_INTERVAL_SECONDS", "86400"))  # 24h par défaut
        self._days_before_expiry = int(os.getenv("WARRANTY_REMINDER_DAYS_BEFORE", "7"))  # 7 jours avant expiration
        self._dry_run = os.getenv("WARRANTY_REMINDER_DRY_RUN", "false").lower() == "true"
        self._default_cc = os.getenv("DEFAULT_COUNTRY_CODE", "+221")

    def start_background(self):
        if not os.getenv("ENABLE_WARRANTY_REMINDERS", "false").lower() == "true":
            print("[WarrantyNotifier] Disabled (ENABLE_WARRANTY_REMINDERS != true)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="WarrantyNotifier", daemon=True)
        self._thread.start()
        print("[WarrantyNotifier] Started background thread")

    def stop_background(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        # Attendre un peu au démarrage pour laisser l'app s'initialiser
        time.sleep(30)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[WarrantyNotifier] Error in tick: {e}")
            self._stop.wait(self._interval_seconds)

    def _tick(self):
        db: Session = SessionLocal()
        try:
            today = date.today()
            reminder_date = today + timedelta(days=self._days_before_expiry)
            
            # Trouver les factures avec garantie qui expire bientôt
            # On cherche celles dont warranty_end_date est entre aujourd'hui et reminder_date
            invoices_expiring = (
                db.query(Invoice)
                .join(Client, Client.client_id == Invoice.client_id, isouter=True)
                .filter(Invoice.has_warranty == True)
                .filter(Invoice.warranty_end_date.isnot(None))
                .filter(and_(
                    Invoice.warranty_end_date >= today,
                    Invoice.warranty_end_date <= reminder_date
                ))
                .all()
            )
            
            print(f"[WarrantyNotifier] Found {len(invoices_expiring)} invoices with warranty expiring soon")
            
            for invoice in invoices_expiring:
                client = invoice.client
                if not client:
                    continue
                
                # Vérifier si on a déjà envoyé un rappel pour cette facture
                if not self._should_notify(db, invoice.invoice_id):
                    continue
                
                self._send_notification(db, invoice, client)
                
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _should_notify(self, db: Session, invoice_id: int) -> bool:
        """Vérifie si on doit envoyer un rappel pour cette facture."""
        key = f"WARRANTY_REMINDER_SENT_{invoice_id}"
        rec = db.query(AppCache).filter(AppCache.cache_key == key).first()
        if rec:
            # Déjà envoyé pour cette période de garantie
            return False
        return True

    def _mark_sent(self, db: Session, invoice_id: int):
        """Marque le rappel comme envoyé pour cette facture."""
        key = f"WARRANTY_REMINDER_SENT_{invoice_id}"
        rec = db.query(AppCache).filter(AppCache.cache_key == key).first()
        now_s = datetime.now().isoformat()
        if not rec:
            rec = AppCache(cache_key=key, cache_value=now_s)
            db.add(rec)
        else:
            rec.cache_value = now_s
        try:
            db.commit()
        except Exception:
            db.rollback()

    def _send_notification(self, db: Session, invoice: Invoice, client: Client):
        """Envoie la notification de fin de garantie."""
        app_name = os.getenv("APP_NAME", "Stock")
        
        # Calculer les jours restants
        today = date.today()
        end_date = invoice.warranty_end_date
        if hasattr(end_date, 'date'):
            end_date = end_date.date()
        days_remaining = (end_date - today).days
        
        # Formater la date de fin
        end_date_str = end_date.strftime("%d/%m/%Y") if end_date else "N/A"
        
        # Construire le message
        lines = [
            f"Bonjour {client.name},",
            "",
            f"📋 {app_name} vous informe que la garantie de votre achat arrive bientôt à expiration.",
            "",
            f"📄 Facture : {invoice.invoice_number}",
            f"📅 Date de fin de garantie : {end_date_str}",
            f"⏳ Jours restants : {days_remaining} jour(s)",
            "",
            f"Durée de garantie : {invoice.warranty_duration} mois",
            "",
            "Si vous rencontrez un problème avec votre produit, nous vous invitons à nous contacter avant l'expiration de la garantie.",
            "",
            "Cordialement,",
            app_name
        ]
        
        body = "\n".join(lines)
        
        if self._dry_run:
            print(f"[WarrantyNotifier] DRY-RUN would send to {client.name} ({client.phone}):\n{body}")
            self._mark_sent(db, invoice.invoice_id)
            return
        
        # Envoyer via WhatsApp
        to_phone = (client.phone or '').strip()
        to_phone = self._normalize_phone(to_phone)
        if not to_phone:
            print(f"[WarrantyNotifier] No phone for client {client.name}, cannot send WhatsApp")
            return
        
        ok = self._send_whatsapp_n8n(to_phone, body, invoice.invoice_id)
        if ok:
            self._mark_sent(db, invoice.invoice_id)
            print(f"[WarrantyNotifier] Sent warranty reminder for invoice {invoice.invoice_number} to {client.name}")
        else:
            print(f"[WarrantyNotifier] Failed to send warranty reminder to {to_phone}")

    def _send_whatsapp_n8n(self, to_phone: str, body: str, invoice_id: int = None) -> bool:
        """Send WhatsApp message via Evolution API (through app proxy). Returns True if successful."""
        app_url = os.getenv("APP_INTERNAL_URL", "http://localhost:8000")
        api_url = f"{app_url}/api/whatsapp/send-text"

        to_norm = self._normalize_phone(to_phone)
        if not to_norm:
            print(f"[WarrantyNotifier] Cannot normalize phone: {to_phone}")
            return False

        payload = {
            'number': to_norm,
            'message': body,
        }

        try:
            data_bytes = json.dumps(payload).encode('utf-8')
            req = _urlrequest.Request(api_url, data=data_bytes, method='POST')
            req.add_header('Content-Type', 'application/json')
            # Les routes /api/whatsapp/* exigent ce jeton depuis le 13/08/2026 :
            # sans lui, ce rappel automatique se ferait refuser en 401.
            _jeton = os.getenv("INTERNAL_API_TOKEN", "").strip()
            if _jeton:
                req.add_header('X-Internal-Token', _jeton)

            with _urlrequest.urlopen(req, timeout=30) as resp:
                resp_body = json.loads(resp.read())
                ok = resp_body.get("success", False)
                if ok:
                    print(f"[WarrantyNotifier] WhatsApp sent to {to_norm} (invoice_id={invoice_id})")
                else:
                    print(f"[WarrantyNotifier] WhatsApp send failed for {to_norm}: {resp_body.get('error', 'unknown')}")
                return ok
        except Exception as e:
            print(f"[WarrantyNotifier] WhatsApp send error: {e}")
            return False

    def _normalize_phone(self, raw: str) -> Optional[str]:
        """Normalize phone to E.164 format."""
        if not raw:
            return None
        s = str(raw).strip()
        for ch in [' ', '-', '(', ')', '.']:
            s = s.replace(ch, '')
        if s.startswith('00'):
            s = '+' + s[2:]
        if s.startswith('+') and s[1:].isdigit():
            return s
        cc_digits = self._default_cc.lstrip('+')
        if s.isdigit() and s.startswith(cc_digits):
            return '+' + s
        if s.startswith('0') and s[1:].isdigit():
            local = s.lstrip('0')
            return f"{self._default_cc}{local}"
        if s.isdigit() and len(s) == 9 and s[0] == '7':
            return f"{self._default_cc}{s}"
        if s.isdigit():
            return f"{self._default_cc}{s}"
        return None


# Singleton
warranty_notifier = WarrantyNotifier()
