from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.locations.models import Location
from apps.locations.services import create_location

User = get_user_model()

SAMPLE_COUNTRY_NAME = "Greece"


class Command(BaseCommand):
    help = (
        "Creates a sample Country > Storage Room > Rack/Shelf tree for local "
        "development. Refuses to run unless DEBUG is enabled."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_locations refuses to run with DEBUG=False (production settings)."
            )

        if Location.objects.filter(
            level=Location.Level.COUNTRY, name__iexact=SAMPLE_COUNTRY_NAME
        ).exists():
            self.stdout.write("Sample location tree already exists; skipping.")
            return

        actor = User.objects.filter(groups__name="Administrator").order_by("id").first()
        if actor is None:
            raise CommandError("No Administrator user found. Run `manage.py seed_dev_data` first.")

        country = create_location(
            level=Location.Level.COUNTRY, name=SAMPLE_COUNTRY_NAME, user=actor
        )
        room = create_location(
            level=Location.Level.STORAGE_ROOM,
            name="2nd Floor Storage Room",
            parent=country,
            user=actor,
        )
        rack = create_location(
            level=Location.Level.RACK_SHELF, name="Rack A", parent=room, user=actor
        )
        create_location(level=Location.Level.RACK_SHELF, name="Shelf 1", parent=room, user=actor)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created sample tree: {country.name} > {room.name} > {rack.name} (and Shelf 1)"
            )
        )
