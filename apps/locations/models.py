from django.contrib.postgres.indexes import GistIndex
from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse

from apps.core.models import TimestampedModel, UUIDPrimaryKeyModel

from .fields import LtreeField


class LocationLevel(models.TextChoices):
    COUNTRY = "country", "Country"
    SITE = "site", "Site/Building"
    FLOOR = "floor", "Floor"
    STORAGE_ROOM = "storage_room", "Storage Room"
    RACK_CABINET = "rack_cabinet", "Rack/Cabinet"
    SHELF_BIN = "shelf_bin", "Shelf/Bin"


class Location(UUIDPrimaryKeyModel, TimestampedModel):
    """Country -> Site -> Floor -> Storage Room -> Rack/Cabinet -> Shelf/Bin,
    as one self-referential table rather than six — see
    docs/architecture/02-data-model.md for the rationale.

    `path` is maintained by a database trigger (0002_location_path_trigger),
    which also enforces the fixed level ordering; `full_clean()`/the service
    layer (see services.py) additionally validate ordering before hitting the
    database, for a clean error message instead of a raw trigger exception.
    """

    Level = LocationLevel

    LEVEL_ORDER = [
        LocationLevel.COUNTRY,
        LocationLevel.SITE,
        LocationLevel.FLOOR,
        LocationLevel.STORAGE_ROOM,
        LocationLevel.RACK_CABINET,
        LocationLevel.SHELF_BIN,
    ]

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    level = models.CharField(max_length=20, choices=LocationLevel.choices)
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30, blank=True)
    path = LtreeField(editable=False, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(level=LocationLevel.COUNTRY, parent__isnull=True)
                    | (~models.Q(level=LocationLevel.COUNTRY) & models.Q(parent__isnull=False))
                ),
                name="location_country_has_no_parent",
            ),
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(level=LocationLevel.COUNTRY),
                name="location_unique_country_name",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "parent",
                "level",
                condition=~models.Q(level=LocationLevel.COUNTRY),
                name="location_unique_sibling_name",
            ),
        ]
        indexes = [
            models.Index(fields=["level", "is_active"], name="location_level_active_idx"),
            models.Index(fields=["name"], name="location_name_idx"),
            GistIndex(fields=["path"], name="location_path_gist_idx"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split())
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("locations:detail", kwargs={"pk": self.pk})

    def ancestors(self):
        """Oldest-first list of ancestor Locations, for breadcrumbs. The
        hierarchy is at most 6 levels deep, so a parent-chain walk is simpler
        and cheap enough that it doesn't need a path-based query.
        """
        result = []
        node = self.parent
        while node is not None:
            result.append(node)
            node = node.parent
        result.reverse()
        return result
