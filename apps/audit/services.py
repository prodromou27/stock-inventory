from .models import AuditEvent


def record_event(
    *,
    actor,
    event_type,
    obj=None,
    summary="",
    old_values=None,
    new_values=None,
    metadata=None,
    ip_address=None,
):
    """The single write path for AuditEvent rows — every app that mutates
    state calls this instead of creating AuditEvent objects directly.
    """
    return AuditEvent.objects.create(
        actor=actor,
        event_type=event_type,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        summary=summary,
        old_values=old_values,
        new_values=new_values,
        metadata=metadata or {},
        ip_address=ip_address,
    )
