"""Shared abstract base models, per docs/architecture/01-repository-structure.md."""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
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


class RecentlyViewed(models.Model):
    """A per-user "last N things you looked at" trail — Product/UnitAsset/
    InventoryTransaction detail views record one row per visit (see
    apps.core.recently_viewed.record_recently_viewed(), called from each of
    those three views). Generic (content_type/object_id) rather than three
    nullable FKs so this stays a apps.core-only model without depending on
    apps.catalog/apps.inventory — docs/architecture/01-repository-structure.
    md's dependency table has catalog/inventory depend on core, never the
    reverse, so a concrete FK to Product/UnitAsset/InventoryTransaction here
    would invert that.

    One row per (user, object) — a repeat view updates `viewed_at` in place
    (apps.core.recently_viewed.record_recently_viewed()) rather than
    growing unbounded, so this table stays exactly as large as "distinct
    objects this user has ever viewed", trimmed for display via
    recently_viewed_for()'s [:limit].

    A purely personal convenience list, not an authorization surface: it
    shows whatever this user has looked at before, the same way a browser's
    own history would, regardless of whether their location access has
    since changed — the linked detail view (get_absolute_url()) is always
    the real, unavoidable access check on click, exactly like every other
    "quick link" in this codebase (see apps.inventory.views._quick_actions_for()'s
    docstring for the same reasoning).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recently_viewed"
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-viewed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "content_type", "object_id"],
                name="recentlyviewed_unique_per_user_object",
            )
        ]
        indexes = [models.Index(fields=["user", "-viewed_at"], name="recentlyviewed_user_idx")]


class SubmissionClaim(models.Model):
    """Single-use stock-form token shared by every application worker."""

    token = models.UUIDField(unique=True)
    claimed_at = models.DateTimeField(auto_now_add=True, db_index=True)
