"""Email center for the admin console.

Outbound: sent over SMTP when configured (SMTP_HOST…). Supports SSL (465) and
STARTTLS (587). Without SMTP the message is still recorded with status
"simulated" so the console works end-to-end in dev.

Sending goes through :class:`SmtpSession`, never through a bare ``smtplib``
call. The reason is a production failure: LWS answered
``421 4.7.0 mail96.lwspanel.com Error: too many connections from <ip>`` in the
middle of a campaign, because every single message opened its own connection
(connect → STARTTLS/SSL → LOGIN → QUIT). Twenty-five recipients meant
twenty-five connections in a few seconds, and the host caps both the number of
simultaneous connections per IP and how fast they may be opened. A session
holds *one* connection open for a whole batch, paces the messages, recycles the
connection every ``SMTP_MAX_PER_CONNECTION`` messages, and retries a temporary
4xx after a backoff instead of turning it into a delivery failure.

Inbound: IMAP sync (LWS mailbox) and/or provider webhook at /admin/email/inbound.
"""
import html as html_lib
import logging
import re
import smtplib
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

from flask import current_app

from app.core.extensions import db
from app.models.email_message import (
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    STATUS_FAILED,
    STATUS_RECEIVED,
    STATUS_SENT,
    STATUS_SIMULATED,
    EmailMessage,
)

logger = logging.getLogger(__name__)

# One process must never hold more SMTP connections than the host tolerates,
# and two threads opening one each is exactly how the 421 was reached. The
# semaphore is sized from config on first use; the pacing clock is shared by
# every session so the *total* rate leaving this process stays under the cap.
_SLOTS_LOCK = threading.Lock()
_slots: threading.BoundedSemaphore | None = None
_slots_size = 0
_PACE_LOCK = threading.Lock()
_last_send_at = 0.0


class SmtpTransientError(RuntimeError):
    """The server said "not now" (4xx, dropped connection, timeout).

    The message did not go out and nothing is wrong with the address: the
    caller should come back later rather than record a delivery failure.
    """

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


def _int_cfg(key: str, default):
    try:
        return int(current_app.config.get(key, default))
    except (RuntimeError, TypeError, ValueError):
        return int(default)


def _float_cfg(key: str, default):
    try:
        return float(current_app.config.get(key, default))
    except (RuntimeError, TypeError, ValueError):
        return float(default)


def _connection_slots() -> threading.BoundedSemaphore:
    global _slots, _slots_size
    size = max(1, _int_cfg("SMTP_MAX_CONNECTIONS", 1))
    with _SLOTS_LOCK:
        if _slots is None or _slots_size != size:
            _slots = threading.BoundedSemaphore(size)
            _slots_size = size
        return _slots


def _response_codes(exc: Exception) -> list[int]:
    """Every SMTP status code an exception carries, whatever its shape."""
    codes = []
    code = getattr(exc, "smtp_code", None)
    if isinstance(code, int):
        codes.append(code)
    payload = getattr(exc, "recipients", None)
    if isinstance(payload, dict):
        for answer in payload.values():
            code = answer[0] if isinstance(answer, (tuple, list)) and answer else None
            if isinstance(code, int):
                codes.append(code)
    return codes


def transient_reason(exc: Exception) -> str | None:
    """``None`` when the failure is permanent, else why it is worth retrying.

    A 4xx is the server asking for patience — a full mailbox, a rate limit, or
    the ``too many connections`` that started all this. A 5xx is a refusal, and
    retrying it only tells the host we do not listen. Codes decide first:
    ``smtplib.SMTPException`` derives from ``OSError``, so the connection-level
    test below would otherwise swallow a permanent rejection.
    """
    codes = _response_codes(exc)
    if codes:
        if all(400 <= c < 500 for c in codes):
            return f"{type(exc).__name__}: {exc}"
        return None
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        # No code at all: the conversation never got far enough to have one —
        # refused connection, dropped socket, timeout. All worth another try.
        return f"{type(exc).__name__}: {exc}"
    return None


def is_configured():
    return bool(current_app.config.get("SMTP_HOST"))


def default_from_addr() -> str:
    return (
        current_app.config.get("EMAIL_FROM")
        or current_app.config.get("SMTP_USER")
        or "contact@pilotcore.fr"
    )


def smtp_from_addr(from_addr: str | None = None) -> str:
    """Adresse d'enveloppe et d'en-tête From — doit correspondre à SMTP_USER (LWS)."""
    fallback = (from_addr or "contact@pilotcore.fr").strip()
    try:
        user = (current_app.config.get("SMTP_USER") or "").strip()
        if user and "@" in user:
            return user
        return (
            from_addr
            or current_app.config.get("EMAIL_FROM")
            or user
            or fallback
        ).strip()
    except RuntimeError:
        return fallback


def send_email(
    to_addr,
    subject,
    body,
    is_html=False,
    tenant_id=None,
    from_addr=None,
    cc_addrs=None,
    in_reply_to_row=None,
    html_body=None,
    reply_to=None,
    list_unsubscribe=None,
    session=None,
):
    """Send (or simulate) an email and record it. Returns the EmailMessage.

    ``session`` — an open :class:`SmtpSession` — puts the send in *batch mode*:
    the message travels on the caller's shared connection, and a temporary
    refusal (4xx, dropped socket) raises :class:`SmtpTransientError` instead of
    being recorded as a delivery failure, so the caller can leave the recipient
    pending and come back later. Without a session the historical behaviour is
    unchanged: one connection for this message, and any error is recorded.
    """
    header_from = smtp_from_addr(from_addr)
    from_addr = header_from

    if html_body:
        html_out = html_body
        plain_out = body or ""
    elif is_html:
        html_out = body or ""
        plain_out = body or ""
    else:
        plain_out = body or ""
        html_out = None
        if plain_out.strip():
            from app.services.transactional_email import wrap_plain_as_html

            html_out = wrap_plain_as_html(subject, plain_out)

    msg_row = EmailMessage(
        direction=DIRECTION_OUTBOUND,
        status="queued",
        from_addr=from_addr,
        to_addr=to_addr,
        cc_addrs=cc_addrs,
        subject=subject,
        body=plain_out,
        html_body=html_out,
        is_html=bool(html_out),
        tenant_id=tenant_id,
    )
    if in_reply_to_row:
        msg_row.in_reply_to_id = in_reply_to_row.id
        msg_row.rfc_in_reply_to = in_reply_to_row.provider_id or make_msgid()
        refs = (in_reply_to_row.references_header or "").strip()
        parent_id = in_reply_to_row.provider_id or ""
        msg_row.references_header = f"{refs} {parent_id}".strip() if parent_id else refs

    from app.services import email_tracking

    if email_tracking.should_track_recipients(to_addr, cc_addrs):
        msg_row.track_token = email_tracking.new_track_token()

    db.session.add(msg_row)
    db.session.commit()

    mime_html, mime_body = email_tracking.instrument_bodies(
        msg_row.track_token,
        html_out,
        plain_out,
        is_html=bool(html_out),
    )

    if not is_configured():
        msg_row.status = STATUS_SIMULATED
        db.session.commit()
        logger.info("Email simulated (SMTP not configured) to=%s subject=%s", to_addr, subject)
        _log(msg_row, simulated=True)
        return msg_row

    try:
        mime = _build_mime(
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=mime_body,
            is_html=bool(html_out),
            html_body=mime_html,
            cc_addrs=cc_addrs,
            in_reply_to=msg_row.rfc_in_reply_to,
            references=msg_row.references_header,
            reply_to=reply_to,
            list_unsubscribe=list_unsubscribe,
        )
        if not msg_row.provider_id:
            msg_row.provider_id = mime.get("Message-ID")

        recipients = [a.strip() for a in to_addr.split(",") if a.strip()]
        if cc_addrs:
            recipients.extend(a.strip() for a in cc_addrs.split(",") if a.strip())

        _smtp_send(from_addr, recipients, mime.as_string(), session=session)
        msg_row.status = STATUS_SENT
        db.session.commit()
        _log(msg_row)
    except SmtpTransientError as exc:
        # Nothing left the building. In batch mode the row would otherwise sit
        # in the outbox as a phantom failure and the retry would create a second
        # one, so it is dropped and the caller decides when to try again.
        if session is not None:
            db.session.delete(msg_row)
            db.session.commit()
            logger.warning("Email différé to=%s: %s", to_addr, exc)
            raise
        msg_row.status = STATUS_FAILED
        msg_row.error = str(exc)[:500]
        db.session.commit()
        logger.warning("Email différé (hors lot) to=%s: %s", to_addr, exc)
        _log(msg_row, error=str(exc))
    except Exception as exc:  # pragma: no cover - depends on live SMTP
        msg_row.status = STATUS_FAILED
        msg_row.error = str(exc)[:500]
        db.session.commit()
        logger.exception("Email send failed to=%s", to_addr)
        _log(msg_row, error=str(exc))
    return msg_row


def _html_to_text(html: str) -> str:
    """Plain-text rendition of an HTML body, for the text/plain alternative.

    The two MIME parts must carry the same content: anti-spam filters (dont
    celui de LWS, basé sur SpamAssassin) pénalisent fortement un texte brut
    très différent — ou beaucoup plus court — que la partie HTML
    (règle ``MPART_ALT_DIFF``).
    """
    if not html:
        return ""
    text = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|tr|table|h[1-6]|li)>", "\n", text)
    # Keep link destinations so the text part carries the same URLs as the HTML.
    text = re.sub(r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _build_mime(
    from_addr,
    to_addr,
    subject,
    body,
    is_html=False,
    html_body=None,
    cc_addrs=None,
    in_reply_to=None,
    references=None,
    reply_to=None,
    list_unsubscribe=None,
):
    if is_html or html_body:
        mime = MIMEMultipart("alternative")
        html_content = html_body or body or ""
        # Toujours dériver le texte brut du HTML : SpamAssassin (filtre LWS)
        # pénalise MPART_ALT_DIFF quand les deux parties divergent, même si le
        # corps brut est long mais formulé différemment du HTML.
        plain = _html_to_text(html_content) if html_content else (body or "").strip()
        mime.attach(MIMEText(plain, "plain", "utf-8"))
        mime.attach(MIMEText(html_content, "html", "utf-8"))
    else:
        mime = MIMEText(body or "", "plain", "utf-8")

    header_from = smtp_from_addr(from_addr)
    mime["Subject"] = subject
    mime["From"] = formataddr(("PilotCore", header_from))
    mime["To"] = to_addr
    if cc_addrs:
        mime["Cc"] = cc_addrs
    # Un message sans Date déclenche MISSING_DATE (+1,4 pt) sur le filtre
    # sortant LWS — assez pour faire bloquer un e-mail par ailleurs sain.
    mime["Date"] = formatdate(localtime=True)
    domain = header_from.split("@")[-1] if "@" in header_from else "pilotcore.fr"
    mime["Message-ID"] = make_msgid(domain=domain)
    if in_reply_to:
        mime["In-Reply-To"] = in_reply_to
    if references:
        mime["References"] = references
    reply_to = reply_to or header_from
    mime["Reply-To"] = reply_to
    if list_unsubscribe:
        mime["List-Unsubscribe"] = list_unsubscribe
        mime["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        mime["Precedence"] = "bulk"
    return mime


def smtp_test():
    """Live connectivity + auth probe against the configured SMTP server.

    Opens the connection (SSL or STARTTLS) and, when credentials are present,
    performs a LOGIN — the exact same steps a real send does, minus the message.
    It borrows a connection slot like any send, so probing while a campaign is
    running can never be the connection that trips the host's limit.
    Returns ``{"ok": bool, "detail": str}`` and never raises.
    """
    cfg = current_app.config
    host = cfg.get("SMTP_HOST")
    if not host:
        return {"ok": False, "detail": "SMTP_HOST non configuré — les envois sont simulés."}
    port = int(cfg.get("SMTP_PORT", 587))
    user = cfg.get("SMTP_USER")
    pwd = cfg.get("SMTP_PASSWORD")
    session = SmtpSession(max_retries=0, slot_timeout=8)
    try:
        session.open()
    except SmtpTransientError as exc:
        logger.warning("SMTP test différé host=%s port=%s: %s", host, port, exc)
        return {"ok": False, "detail": str(exc)[:250]}
    except Exception as exc:  # pragma: no cover - depends on live SMTP
        logger.warning("SMTP test failed host=%s port=%s: %s", host, port, exc)
        return {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:250]}"}
    finally:
        session.close()
    if user and pwd:
        return {"ok": True, "detail": f"Connexion et authentification OK ({host}:{port})."}
    if user and not pwd:
        return {"ok": False, "detail": f"Connexion OK ({host}:{port}) mais SMTP_PASSWORD manquant."}
    return {"ok": True, "detail": f"Connexion OK ({host}:{port}) — sans authentification."}


def _connect():
    """Open and authenticate one SMTP connection. Raises like ``smtplib`` does."""
    cfg = current_app.config
    host = cfg["SMTP_HOST"]
    port = int(cfg.get("SMTP_PORT", 587))
    use_ssl = cfg.get("SMTP_USE_SSL", False)
    use_tls = cfg.get("SMTP_USE_TLS", True)

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    try:
        if not use_ssl and use_tls:
            server.starttls()
        user = cfg.get("SMTP_USER")
        pwd = cfg.get("SMTP_PASSWORD")
        if user and pwd:
            server.login(user, pwd)
    except Exception:
        try:
            server.close()
        except Exception:
            pass
        raise
    return server


class SmtpSession:
    """One SMTP connection, reused for many messages.

    Open it once around a batch and hand it to :func:`send_email`; every message
    then travels on the same connection instead of dialling the server again.
    The session also owns the three things that keep LWS from answering 421:
    a bounded number of connections per process, a minimum delay between
    messages, and a recycle after ``SMTP_MAX_PER_CONNECTION`` messages (hosts
    that cap messages-per-connection drop the socket silently otherwise).
    """

    def __init__(self, *, pace: float | None = None, max_messages: int | None = None,
                 max_retries: int | None = None, slot_timeout: float | None = None):
        self.pace = _float_cfg("SMTP_SEND_INTERVAL", 1.2) if pace is None else float(pace)
        self.max_messages = (
            _int_cfg("SMTP_MAX_PER_CONNECTION", 25) if max_messages is None else int(max_messages)
        )
        self.max_retries = (
            _int_cfg("SMTP_MAX_RETRIES", 2) if max_retries is None else int(max_retries)
        )
        self.backoff = _float_cfg("SMTP_RETRY_BACKOFF", 5)
        self.slot_timeout = (
            _float_cfg("SMTP_SLOT_TIMEOUT", 60) if slot_timeout is None else float(slot_timeout)
        )
        self.sent = 0
        self._server = None
        self._on_connection = 0
        self._slot = None

    # ------------------------------------------------------------- lifecycle
    def __enter__(self) -> "SmtpSession":
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def _acquire_slot(self):
        if self._slot is not None:
            return
        slots = _connection_slots()
        if not slots.acquire(timeout=self.slot_timeout):
            raise SmtpTransientError(
                "Toutes les connexions SMTP autorisées sont occupées — réessayez dans un instant.",
                retry_after=60,
            )
        self._slot = slots

    def _release_slot(self):
        if self._slot is None:
            return
        try:
            self._slot.release()
        except ValueError:  # pragma: no cover - only if released twice
            pass
        self._slot = None

    def open(self, *, retries: int | None = None):
        """Connect now, so a host that is refusing connections is known before
        a single message row is written.

        ``retries`` bounds how long this may block: a caller inside a web
        request wants ``0`` — one attempt, then a "come back later" — rather
        than a minute of backoff behind an unanswered socket.
        """
        if self._server is not None:
            return
        tries = self.max_retries if retries is None else max(0, int(retries))
        self._acquire_slot()
        last_exc = None
        for attempt in range(tries + 1):
            try:
                self._server = _connect()
                self._on_connection = 0
                return
            except Exception as exc:  # noqa: BLE001 — classified just below
                last_exc = exc
                reason = transient_reason(exc)
                if reason is None or attempt >= tries:
                    break
                logger.warning(
                    "SMTP connexion refusée (essai %s/%s) : %s",
                    attempt + 1, tries + 1, reason,
                )
                time.sleep(self.backoff * (attempt + 1))
        self._release_slot()
        if transient_reason(last_exc) is not None:
            raise SmtpTransientError(
                f"Serveur SMTP indisponible — {last_exc}", retry_after=120
            ) from last_exc
        raise last_exc

    def _drop(self):
        """Forget the connection. A socket that just failed is never reused."""
        server, self._server = self._server, None
        if server is None:
            return
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass

    def close(self):
        self._drop()
        self._release_slot()

    # ---------------------------------------------------------------- pacing
    def _wait_turn(self):
        """Space messages out, counting from the last one *any* session sent."""
        global _last_send_at
        if self.pace <= 0:
            return
        with _PACE_LOCK:
            wait = self.pace - (time.monotonic() - _last_send_at)
            if wait > 0:
                time.sleep(wait)
            _last_send_at = time.monotonic()

    # ------------------------------------------------------------------ send
    def send(self, from_addr, recipients, raw_message):
        """Send one message, reconnecting and retrying on a temporary refusal.

        Raises :class:`SmtpTransientError` when the server keeps saying "later",
        and the original ``smtplib`` exception when the refusal is permanent.
        """
        last_exc = None
        envelope_from = smtp_from_addr(from_addr)
        for attempt in range(self.max_retries + 1):
            try:
                if self._server is None or (
                    self.max_messages and self._on_connection >= self.max_messages
                ):
                    self._drop()
                    # No inner backoff: this loop already owns the retries.
                    self.open(retries=0)
                self._wait_turn()
                self._server.sendmail(envelope_from, recipients, raw_message)
                self._on_connection += 1
                self.sent += 1
                return
            except SmtpTransientError:
                raise
            except Exception as exc:  # noqa: BLE001 — classified just below
                last_exc = exc
                reason = transient_reason(exc)
                if reason is None:
                    # A refusal with a status code leaves the connection in a
                    # known state — ``smtplib`` has already sent RSET — so it is
                    # kept: dropping it would open a fresh connection for every
                    # bad address, which is the churn this class exists to stop.
                    if not _response_codes(exc):
                        self._drop()
                    raise
                # A temporary refusal or a dropped socket leaves the session out
                # of step with the server. Start the next attempt clean.
                self._drop()
                if attempt >= self.max_retries:
                    break
                logger.warning(
                    "SMTP envoi différé (essai %s/%s) : %s",
                    attempt + 1, self.max_retries + 1, reason,
                )
                time.sleep(self.backoff * (attempt + 1))
        raise SmtpTransientError(
            f"Serveur SMTP saturé — {last_exc}", retry_after=120
        ) from last_exc


def smtp_session(**kwargs) -> SmtpSession:
    """A session to wrap around a batch: ``with admin_email.smtp_session() as s``."""
    return SmtpSession(**kwargs)


def _smtp_send(from_addr, recipients, raw_message, session: SmtpSession | None = None):
    if session is not None:
        session.send(from_addr, recipients, raw_message)
        return
    with SmtpSession() as one_shot:
        one_shot.send(from_addr, recipients, raw_message)


def store_inbound(
    from_addr,
    to_addr,
    subject,
    body,
    provider_id=None,
    html_body=None,
    is_html=False,
    cc_addrs=None,
    imap_uid=None,
    imap_folder=None,
    attachments=None,
):
    """Persist an inbound email (IMAP or webhook)."""
    if provider_id:
        existing = EmailMessage.query.filter_by(provider_id=provider_id).first()
        if existing:
            return existing
    if imap_uid and imap_folder:
        existing = EmailMessage.query.filter_by(imap_uid=imap_uid, imap_folder=imap_folder).first()
        if existing:
            return existing

    import json

    row = EmailMessage(
        direction=DIRECTION_INBOUND,
        status=STATUS_RECEIVED,
        from_addr=from_addr,
        to_addr=to_addr,
        cc_addrs=cc_addrs,
        subject=subject,
        body=body,
        html_body=html_body,
        is_html=is_html or bool(html_body),
        provider_id=provider_id,
        imap_uid=imap_uid,
        imap_folder=imap_folder,
        attachments_json=json.dumps(attachments) if attachments else None,
    )
    db.session.add(row)
    db.session.commit()
    from app.services.events import CAT_EMAIL, log_event

    log_event(
        CAT_EMAIL,
        "email_received",
        summary=f"Reçu de {from_addr} — {subject or '(sans objet)'}",
        actor=from_addr,
    )
    return row


def _log(msg_row, simulated=False, error=None):
    from app.services.events import CAT_EMAIL, LEVEL_ERROR, LEVEL_INFO, log_event

    if error:
        log_event(
            CAT_EMAIL,
            "email_failed",
            summary=f"Échec envoi à {msg_row.to_addr} — {error}",
            level=LEVEL_ERROR,
        )
    else:
        log_event(
            CAT_EMAIL,
            "email_sent",
            summary=f"Envoyé à {msg_row.to_addr} — {msg_row.subject or '(sans objet)'}"
            + (" (simulé)" if simulated else ""),
            level=LEVEL_INFO,
        )
