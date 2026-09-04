from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import (
    ADMINISTRATOR,
    READ_ONLY_USER,
    STOCK_MANAGER,
    require_role,
)

from .models import MustChangePassword, UserLocationAccess

User = get_user_model()

VALID_ROLES = (ADMINISTRATOR, STOCK_MANAGER, READ_ONLY_USER)


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


@transaction.atomic
def create_user(*, created_by, username, password, role, force_password_change=True):
    """Administrator-only. Creates a local account and assigns it exactly one
    role Group in the same step — today's UI had no create-user screen at
    all (only grant/revoke *location* access for an already-existing user),
    so an Administrator had no supported way to add a teammate without
    reaching for `manage.py shell`/Django admin, bypassing this app's own
    audit trail. `force_password_change=True` (default) reuses the same
    MustChangePassword mechanism `bootstrap_admin` already establishes for
    the default admin account — an Administrator-set password is always
    provisional until the new user picks their own.
    """
    require_role(created_by, ADMINISTRATOR)
    if role not in VALID_ROLES:
        raise ValidationError(f"'{role}' is not a valid role.")

    user = User(username=username)
    user.set_password(password)
    user.full_clean(exclude=["password"])
    user.save()
    user.groups.add(Group.objects.get(name=role))
    if force_password_change:
        MustChangePassword.objects.get_or_create(user=user)

    record_event(
        actor=created_by,
        event_type=AuditEvent.EventType.RECORD_CREATED,
        obj=user,
        summary=f"Created user '{username}' with role {role}",
        new_values={"username": username, "role": role},
    )
    return user


@transaction.atomic
def set_user_role(*, user, role, changed_by):
    """Administrator-only. Each user has exactly one role Group at a time
    (apps.core.authorization checks group membership, not a rank/level) —
    this removes any of the three role groups the user currently holds and
    adds the new one, so a role change is a clean replacement, never an
    accumulation of stale group memberships.
    """
    require_role(changed_by, ADMINISTRATOR)
    if role not in VALID_ROLES:
        raise ValidationError(f"'{role}' is not a valid role.")

    old_roles = list(user.groups.filter(name__in=VALID_ROLES).values_list("name", flat=True))
    if old_roles == [role]:
        return user  # already exactly this role — no-op, nothing to audit

    user.groups.remove(*Group.objects.filter(name__in=VALID_ROLES))
    user.groups.add(Group.objects.get(name=role))

    record_event(
        actor=changed_by,
        event_type=AuditEvent.EventType.PERMISSION_CHANGED,
        obj=user,
        summary=f"Changed {user}'s role to {role}",
        old_values={"role": old_roles[0] if old_roles else None},
        new_values={"role": role},
    )
    return user


@transaction.atomic
def set_user_active(*, user, is_active, changed_by):
    """Deactivate/reactivate — never delete (a User is referenced by
    on_delete=PROTECT everywhere in the append-only ledger, so deletion was
    never actually viable once a user has performed any transaction; this is
    the one real, audited path). Django's own auth backend already refuses
    login for is_active=False, so this alone is sufficient to cut off access.
    """
    require_role(changed_by, ADMINISTRATOR)
    if user.is_active == is_active:
        return user

    old_active = user.is_active
    user.is_active = is_active
    user.save(update_fields=["is_active"])
    record_event(
        actor=changed_by,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=user,
        summary=f"{'Reactivated' if is_active else 'Deactivated'} user '{user.username}'",
        old_values={"is_active": old_active},
        new_values={"is_active": is_active},
    )
    return user
