from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role

from .models import Location


def normalize_name(name):
    return " ".join((name or "").split())


def _expected_parent_level(level):
    index = Location.LEVEL_ORDER.index(level)
    if index == 0:
        return None
    return Location.LEVEL_ORDER[index - 1]


@transaction.atomic
def create_location(*, level, name, user, parent=None, code=""):
    """Administrator-only. Validates level ordering before writing, so the
    error the user sees is a clear ValidationError rather than the database
    trigger's exception (docs/architecture/02-data-model.md).
    """
    require_role(user, ADMINISTRATOR)

    name = normalize_name(name)
    if not name:
        raise ValidationError("Name is required.")

    expected_parent_level = _expected_parent_level(level)
    if expected_parent_level is None:
        if parent is not None:
            raise ValidationError("A Country may not have a parent location.")
    else:
        if parent is None or parent.level != expected_parent_level:
            expected_label = Location.Level(expected_parent_level).label
            raise ValidationError(
                f"A {Location.Level(level).label} must be created under a {expected_label}."
            )
        if not parent.is_active:
            raise ValidationError("Cannot create a location under an inactive parent.")

    location = Location(level=level, name=name, code=code, parent=parent)
    location.full_clean(exclude=["path"])
    location.save()
    location.refresh_from_db(fields=["path"])

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=location,
        summary=f"Created {location.get_level_display()} '{location.name}'"
        + (f" under '{parent.name}'" if parent else ""),
        new_values={
            "level": location.level,
            "name": location.name,
            "code": location.code,
            "parent_id": str(parent.pk) if parent else None,
        },
    )
    return location


@transaction.atomic
def deactivate_location(*, location, user):
    require_role(user, ADMINISTRATOR)
    if not location.is_active:
        return location

    location.is_active = False
    location.save(update_fields=["is_active", "updated_at"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=location,
        summary=f"Deactivated {location.get_level_display()} '{location.name}'",
        old_values={"is_active": True},
        new_values={"is_active": False},
    )
    return location


@transaction.atomic
def reactivate_location(*, location, user):
    require_role(user, ADMINISTRATOR)
    if location.is_active:
        return location

    location.is_active = True
    location.save(update_fields=["is_active", "updated_at"])
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=location,
        summary=f"Reactivated {location.get_level_display()} '{location.name}'",
        old_values={"is_active": False},
        new_values={"is_active": True},
    )
    return location
