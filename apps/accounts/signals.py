from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR


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


User = get_user_model()


@receiver(m2m_changed, sender=User.groups.through)
def sync_is_staff_with_administrator_group(sender, instance, action, **kwargs):
    """Administrator-role users must be able to reach Django's built-in
    /admin/ site — that's how a new user account gets created and assigned a
    role at all today (spec §14's "User and permission administration"
    screen; apps.accounts' own screens only cover the location-scoped access
    grants that need custom audited business logic, not plain user/role
    CRUD, which Django's stock User/Group admin already does well). Without
    this, only the original `createsuperuser` account could ever reach
    /admin/, since nothing else grants `is_staff`.

    `is_staff` is not itself an authorization boundary anywhere in this
    app — every view checks the Administrator group via
    apps.core.authorization, never is_staff/is_superuser alone — so this
    only keeps admin-site *reachability* in sync with that role. Superusers
    are left untouched either way.
    """
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if not isinstance(instance, User) or instance.is_superuser:
        return

    is_administrator = instance.groups.filter(name=ADMINISTRATOR).exists()
    if instance.is_staff != is_administrator:
        instance.is_staff = is_administrator
        instance.save(update_fields=["is_staff"])
