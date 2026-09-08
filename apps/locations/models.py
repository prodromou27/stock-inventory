from django.contrib.postgres.indexes import GistIndex
from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse

from apps.core.models import TimestampedModel, UUIDPrimaryKeyModel

from .fields import LtreeField


class LocationLevel(models.TextChoices):
    COUNTRY = "country", "Country"
    STORAGE_ROOM = "storage_room", "Storage Room"
    RACK_SHELF = "rack_shelf", "Floor (Shelf/Rack)"


class Location(UUIDPrimaryKeyModel, TimestampedModel):
    """Country -> Storage Room -> Rack/Shelf, as one self-referential table
    rather than three — see docs/architecture/02-data-model.md for the
    rationale (originally six levels — Country/Site/Floor/Storage Room/
    Rack-Cabinet/Shelf-Bin — collapsed to these three by direct instruction:
    Site and Floor added authorization-boundary depth nobody used, and
    Rack/Cabinet vs Shelf/Bin was a distinction without a difference for how
    stock is actually tracked).

    `path` is maintained by a database trigger (0002_location_path_trigger),
    which also enforces the fixed level ordering; `full_clean()`/the service
    layer (see services.py) additionally validate ordering before hitting the
    database, for a clean error message instead of a raw trigger exception.
    """

    Level = LocationLevel

    LEVEL_ORDER = [
        LocationLevel.COUNTRY,
        LocationLevel.STORAGE_ROOM,
        LocationLevel.RACK_SHELF,
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
        hierarchy is at most 3 levels deep, so a parent-chain walk is simpler
        and cheap enough that it doesn't need a path-based query.
        """
        result = []
        node = self.parent
        while node is not None:
            result.append(node)
            node = node.parent
        result.reverse()
        return result


def order_by_hierarchy(queryset):
    """Orders a Location queryset by actual hierarchy depth (Country,
    Storage Room, Rack/Shelf), then name.

    Plain `.order_by("level", "name")` — used to scatter across a dozen
    call sites in this codebase — sorts alphabetically by `level`'s
    *stored string* ("country" < "rack_shelf" < "storage_room"), which is
    not the hierarchy order at all: a location picker built that way lists
    every Country, then every Rack/Shelf, then every Storage Room —
    scrambled relative to how an operator actually thinks about the tree.
    This annotates each row with its real position in Location.LEVEL_ORDER
    instead.
    """
    ordering = models.Case(
        *(
            models.When(level=level, then=models.Value(index))
            for index, level in enumerate(Location.LEVEL_ORDER)
        ),
        output_field=models.IntegerField(),
    )
    return queryset.annotate(_level_rank=ordering).order_by("_level_rank", "name")


# The canonical set of "a real place stock can be held, and the only
# levels a Stock Manager may create" — used to be re-typed as an identical
# tuple in apps/inventory/forms.py, apps/imports/forms.py, apps/imports/
# location_resolution.py, apps/dataquality/checks.py, and
# apps/locations/services.py/forms.py/views.py — the same class of
# duplication-drift bug as the scattered `.order_by("level", "name")` calls
# order_by_hierarchy() above replaced. Import from here instead of
# re-declaring.
#
# Every "is this too high to hold stock" check must test `not in
# ROOM_OR_BELOW_LEVELS` (deny-by-default), never a positive enumeration of
# "the levels above room" — a Location can carry a level value outside the
# *current* LEVEL_ORDER entirely (0003_alter_location_level.py leaves a
# pre-collapse Site/Floor row in place, un-deleted, exactly when a
# historical ledger record still references it — ledger tables are
# append-only and can never be re-pointed to make room for the delete), and
# an allow-by-default positive list silently treats that orphaned row as a
# valid stock location instead of flagging it.
ROOM_OR_BELOW_LEVELS = (LocationLevel.STORAGE_ROOM, LocationLevel.RACK_SHELF)
