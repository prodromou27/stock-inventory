from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from apps.audit.models import AuditEvent
from apps.audit.services import record_event


def _client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def log_login_success(sender, request, user, **kwargs):
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.LOGIN_SUCCESS,
        summary=f"{user.get_username()} logged in",
        ip_address=_client_ip(request),
    )


@receiver(user_login_failed)
def log_login_failure(sender, credentials, request=None, **kwargs):
    username = credentials.get("username", "") if credentials else ""
    record_event(
        actor=None,
        event_type=AuditEvent.EventType.LOGIN_FAILURE,
        summary=f"Failed login attempt for username '{username}'",
        ip_address=_client_ip(request),
    )
