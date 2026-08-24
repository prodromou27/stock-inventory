from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role

from .models import UserLocationAccess


@transaction.atomic
def grant_location_access(*, user, location, granted_by):
    require_role(granted_by, ADMINISTRATOR)

    access, created = UserLocationAccess.objects.get_or_create(
        user=user,
        location=location,
        defaults={"granted_by": granted_by},
    )
    if created:
        record_event(
            actor=granted_by,
            event_type=AuditEvent.EventType.PERMISSION_CHANGED,
            obj=access,
            summary=f"Granted {user} access to {location}",
            new_values={"user": user.username, "location": str(location)},
        )
    return access


@transaction.atomic
def revoke_location_access(*, access, revoked_by):
    require_role(revoked_by, ADMINISTRATOR)

    summary = f"Revoked {access.user} access to {access.location}"
    old_values = {"user": access.user.username, "location": str(access.location)}
    access.delete()
    record_event(
        actor=revoked_by,
        event_type=AuditEvent.EventType.PERMISSION_CHANGED,
        summary=summary,
        old_values=old_values,
    )
