"""Shared abstract base models, per docs/architecture/01-repository-structure.md."""

import uuid

from django.conf import settings
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UserStampedModel(TimestampedModel):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        on_delete=models.PROTECT,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        on_delete=models.PROTECT,
    )

    class Meta:
        abstract = True


class UUIDPrimaryKeyModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class AppendOnlyQuerySet(models.QuerySet):
    """Blocks the bulk-operation paths that bypass AppendOnlyModel.save()/delete()."""

    def update(self, **kwargs):
        raise ValueError("These rows are append-only; bulk update() is not permitted.")

    def delete(self):
        raise ValueError("These rows are append-only; bulk delete() is not permitted.")


class AppendOnlyModel(models.Model):
    """Rows are INSERT-only from the application's perspective — ledger and
    audit tables (docs/architecture/02-data-model.md's deletion policy: "Never
    deleted; append-only"). Full defense-in-depth via a separate, low-privilege
    runtime database role with UPDATE/DELETE revoked is a Phase 8 hardening
    item — the migration-owning role is necessarily the same role the app
    connects as today, so a same-role REVOKE would have no effect (table
    owners bypass GRANT/REVOKE in PostgreSQL).

    Subclasses must set `objects = AppendOnlyQuerySet.as_manager()` to also
    block bulk update()/delete() (this class alone only guards the
    instance-level save()/delete() path).
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(
                f"{self._meta.object_name} rows are append-only and cannot be updated "
                "after creation."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{self._meta.object_name} rows are append-only and cannot be deleted.")
