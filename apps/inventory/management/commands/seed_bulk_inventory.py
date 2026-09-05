from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.catalog.models import Brand, ItemCategory, Product, ProductType, TrackingMethod
from apps.inventory.models import StockBalance, UnitAsset, UnitStatus
from apps.locations.models import Location

User = get_user_model()

# A dedicated bulk_create-based seed for performance/pagination testing at
# spec §17/§21.15's 8,000+-row scale — deliberately NOT routed through
# apps.inventory.services.receipts.receive_stock() (doc 08's "seeded... via a
# management command" is explicitly a separate path from the interactive
# service layer; individual service calls for 8,000+ rows would be far too
# slow for a seed command, and no ledger/audit trail is needed for synthetic
# perf-test data).


class Command(BaseCommand):
    help = (
        "Bulk-creates unit assets and a quantity balance for performance/pagination "
        "testing (spec acceptance criterion #15: responsive at 8,000+ records). "
        "Refuses to run unless DEBUG=True."
    )

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=8000)

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_bulk_inventory refuses to run with DEBUG=False (production settings)."
            )

        count = options["count"]

        admin = User.objects.filter(groups__name="Administrator").order_by("id").first()
        if admin is None:
            raise CommandError("No Administrator user found. Run `manage.py seed_dev_data` first.")

        room = Location.objects.filter(level=Location.Level.STORAGE_ROOM, is_active=True).first()
        if room is None:
            raise CommandError("No storage room found. Run `manage.py seed_locations` first.")

        brand, _ = Brand.objects.get_or_create(name="BulkTest Brand")
        product_type, _ = ProductType.objects.get_or_create(name="BulkTest Type")
        product, _ = Product.objects.get_or_create(
            brand=brand,
            normalized_model="bulk test model",
            defaults={
                "model": "Bulk Test Model",
                "product_type": product_type,
                "tracking_method": TrackingMethod.UNIT,
                "category": ItemCategory.SERIALIZED_ASSET,
                "created_by": admin,
                "updated_by": admin,
            },
        )

        existing = UnitAsset.objects.filter(product=product).count()
        to_create = max(0, count - existing)

        if to_create == 0:
            self.stdout.write(f"Already have {existing} bulk-test unit assets; nothing to do.")
            return

        arrival_date = timezone.localdate()
        batch = []
        created = 0
        for i in range(existing, existing + to_create):
            serial = f"BULK-{i:08d}"
            batch.append(
                UnitAsset(
                    product=product,
                    vendor_serial=serial,
                    normalized_serial=serial,
                    status=UnitStatus.IN_STOCK,
                    current_location=room,
                    arrival_date=arrival_date,
                    created_by=admin,
                    updated_by=admin,
                )
            )
            if len(batch) >= 1000:
                UnitAsset.objects.bulk_create(batch)
                created += len(batch)
                batch = []
        if batch:
            UnitAsset.objects.bulk_create(batch)
            created += len(batch)

        quantity_product, _ = Product.objects.get_or_create(
            brand=brand,
            normalized_model="bulk test consumable",
            defaults={
                "model": "Bulk Test Consumable",
                "product_type": product_type,
                "tracking_method": TrackingMethod.QUANTITY,
                "category": ItemCategory.QUANTITY_STOCK,
                "created_by": admin,
                "updated_by": admin,
            },
        )
        StockBalance.objects.get_or_create(
            product=quantity_product, location=room, defaults={"on_hand_quantity": count}
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Bulk-created {created} unit assets ({existing + created} total for this product) "
                f"at {room}."
            )
        )
