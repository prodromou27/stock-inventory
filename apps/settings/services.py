import os
import ssl
import tempfile

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role

from .models import SystemSettings

MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024
MAX_CERTIFICATE_FILE_SIZE_BYTES = 5 * 1024 * 1024
_LOGO_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def sniff_logo_content_type(file_obj):
    """Never trusts the client-supplied Content-Type — same magic-byte
    pattern as apps.documents.pdf.sniff_logo_content_type. Duplicated
    rather than imported so apps.settings (like apps.core) stays
    dependency-free of the more specific apps.documents app.
    """
    file_obj.seek(0)
    header = file_obj.read(16)
    file_obj.seek(0)
    for signature, content_type in _LOGO_SIGNATURES:
        if header.startswith(signature):
            return content_type
    return None


def _validate_logo(logo_file):
    if logo_file.size > MAX_LOGO_SIZE_BYTES:
        raise ValidationError("Logo file exceeds the 2 MB size limit.")
    if sniff_logo_content_type(logo_file) is None:
        raise ValidationError("Logo must be a PNG or JPEG image.")


@transaction.atomic
def update_system_settings(
    *, user, site_name, allowed_hosts_override, accent_color="", logo=None, remove_logo=False
):
    require_role(user, ADMINISTRATOR)

    site_name = (site_name or "").strip()
    allowed_hosts_override = (allowed_hosts_override or "").strip()
    accent_color = (accent_color or "").strip().lower()
    if logo is not None:
        _validate_logo(logo)

    settings_obj = SystemSettings.load()
    old_values = {
        "site_name": settings_obj.site_name,
        "allowed_hosts_override": settings_obj.allowed_hosts_override,
        "accent_color": settings_obj.accent_color,
    }
    settings_obj.site_name = site_name or "Stock Inventory"
    settings_obj.allowed_hosts_override = allowed_hosts_override
    settings_obj.accent_color = accent_color
    settings_obj.updated_by = user
    if logo is not None:
        settings_obj.logo = logo
    elif remove_logo and settings_obj.logo:
        settings_obj.logo.delete(save=False)
        settings_obj.logo = None
    settings_obj.full_clean()
    settings_obj.save()

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=settings_obj,
        summary="Updated system settings",
        old_values=old_values,
        new_values={
            "site_name": settings_obj.site_name,
            "allowed_hosts_override": settings_obj.allowed_hosts_override,
            "accent_color": settings_obj.accent_color,
        },
    )
    return settings_obj


@transaction.atomic
def update_timezone_settings(*, user, timezone):
    require_role(user, ADMINISTRATOR)

    timezone = (timezone or "").strip()

    settings_obj = SystemSettings.load()
    old_values = {"timezone": settings_obj.timezone}
    settings_obj.timezone = timezone
    settings_obj.updated_by = user
    settings_obj.full_clean()
    settings_obj.save()

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=settings_obj,
        summary="Updated business timezone",
        old_values=old_values,
        new_values={"timezone": settings_obj.timezone},
    )
    return settings_obj


@transaction.atomic
def update_smtp_settings(
    *, user, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_tls, smtp_from_email
):
    require_role(user, ADMINISTRATOR)

    settings_obj = SystemSettings.load()
    # Never logs smtp_password — old_values/new_values record whether a
    # password is set, not its value, so the audit trail never carries a
    # secret in cleartext.
    old_values = {
        "smtp_host": settings_obj.smtp_host,
        "smtp_port": settings_obj.smtp_port,
        "smtp_username": settings_obj.smtp_username,
        "smtp_password_set": bool(settings_obj.smtp_password),
        "smtp_use_tls": settings_obj.smtp_use_tls,
        "smtp_from_email": settings_obj.smtp_from_email,
    }
    settings_obj.smtp_host = (smtp_host or "").strip()
    settings_obj.smtp_port = smtp_port
    settings_obj.smtp_username = (smtp_username or "").strip()
    settings_obj.smtp_password = smtp_password or ""
    settings_obj.smtp_use_tls = bool(smtp_use_tls)
    settings_obj.smtp_from_email = (smtp_from_email or "").strip()
    settings_obj.updated_by = user
    settings_obj.full_clean()
    settings_obj.save()

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=settings_obj,
        summary="Updated SMTP settings",
        old_values=old_values,
        new_values={
            "smtp_host": settings_obj.smtp_host,
            "smtp_port": settings_obj.smtp_port,
            "smtp_username": settings_obj.smtp_username,
            "smtp_password_set": bool(settings_obj.smtp_password),
            "smtp_use_tls": settings_obj.smtp_use_tls,
            "smtp_from_email": settings_obj.smtp_from_email,
        },
    )
    return settings_obj


def send_test_email(*, recipient):
    """Sends one plain-text message using the currently-saved SMTP settings
    — the only thing in this app that ever sends email (deliberately: no
    password-reset-by-email or notification wiring was asked for, see
    docs/architecture's Settings section). Raises a plain Exception with the
    underlying error on failure so the view can show it directly; doesn't
    roll back a settings save that already succeeded, since a bad test
    recipient shouldn't cost the operator their otherwise-valid SMTP config.
    """
    from django.core.mail import EmailMessage, get_connection

    settings_obj = SystemSettings.load()
    if not settings_obj.smtp_host:
        raise ValidationError("Configure and save an SMTP host before sending a test email.")

    connection = get_connection(
        backend=django_settings.EMAIL_BACKEND,
        host=settings_obj.smtp_host,
        port=settings_obj.smtp_port or 587,
        username=settings_obj.smtp_username,
        password=settings_obj.smtp_password,
        use_tls=settings_obj.smtp_use_tls,
    )
    message = EmailMessage(
        subject="Stock Inventory — test email",
        body="This is a test email from the Stock Inventory application's SMTP settings.",
        from_email=settings_obj.smtp_from_email or settings_obj.smtp_username or None,
        to=[recipient],
        connection=connection,
    )
    message.send()


def _validate_cert_key_pair(cert_bytes, key_bytes):
    """Structural validation only (well-formed PEM, cert/key actually
    match) via the stdlib ssl module — no claim about CA trust or
    expiry, which nginx will simply fail to serve on if wrong, same as
    today's manual file-drop process.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        cert_path = os.path.join(tmp_dir, "fullchain.pem")
        key_path = os.path.join(tmp_dir, "privkey.pem")
        with open(cert_path, "wb") as f:
            f.write(cert_bytes)
        with open(key_path, "wb") as f:
            f.write(key_bytes)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(cert_path, key_path)
        except ssl.SSLError as exc:
            raise ValidationError(f"Certificate/key are not a valid, matching pair: {exc}") from exc


@transaction.atomic
def update_certificate(*, user, cert_file, key_file):
    """Administrator-only. Writes to the host-mounted certs directory shared
    (read-write here, read-only in `proxy`) with the nginx reverse proxy —
    settings.CERTS_DIR, kept in sync with deploy/docker-compose.prod.yml's
    `web` service mount (config/settings/base.py's default matches that
    mount's container-side path; tests override it to a tmp_path). Does NOT
    reload nginx: `proxy` only re-reads these files on its own restart, so
    the operator still needs to run `docker compose -f
    deploy/docker-compose.prod.yml restart proxy` afterward (documented in
    the view/template and deploy/DEPLOYMENT.md) — automating that would need
    the web container to control the Docker daemon (a docker.sock mount), a
    security trade-off not worth making for this.
    """
    require_role(user, ADMINISTRATOR)

    if cert_file.size > MAX_CERTIFICATE_FILE_SIZE_BYTES:
        raise ValidationError("Certificate files must be 5 MB or smaller.")
    if key_file.size > MAX_CERTIFICATE_FILE_SIZE_BYTES:
        raise ValidationError("Private key files must be 5 MB or smaller.")

    cert_bytes = cert_file.read()
    key_bytes = key_file.read()
    _validate_cert_key_pair(cert_bytes, key_bytes)

    certs_dir = django_settings.CERTS_DIR
    os.makedirs(certs_dir, exist_ok=True)
    cert_path = os.path.join(certs_dir, "fullchain.pem")
    key_path = os.path.join(certs_dir, "privkey.pem")
    with open(cert_path, "wb") as f:
        f.write(cert_bytes)
    with open(key_path, "wb") as f:
        f.write(key_bytes)
    os.chmod(key_path, 0o600)

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=None,
        summary="Uploaded a new TLS certificate (proxy restart still required to take effect)",
    )
