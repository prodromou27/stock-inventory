"""Seeds one Customer row per distinct historical InventoryTransaction.final_customer
value, so the new customer search/autofill (apps.inventory.views.CustomerSearchDataView)
has real data from day one. Pure addition — no InventoryTransaction row is touched or
linked; final_customer stays exactly as it was on every existing transaction.
"""

from django.db import migrations


def backfill_customers(apps, schema_editor):
    InventoryTransaction = apps.get_model("inventory", "InventoryTransaction")
    Customer = apps.get_model("inventory", "Customer")

    names = (
        InventoryTransaction.objects.exclude(final_customer="")
        .order_by()
        .values_list("final_customer", flat=True)
        .distinct()
    )
    existing = set(Customer.objects.values_list("name", flat=True))
    Customer.objects.bulk_create(Customer(name=name) for name in names if name not in existing)


def noop_reverse(apps, schema_editor):
    """Deliberately not reversed — these rows may already be in real use
    (search/autofill, or manually edited) by the time anyone reverses this
    migration; reversing would need to guess which rows are still "just the
    backfill" versus genuinely wanted, which isn't a safe automated call.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0010_add_customer"),
    ]

    operations = [
        migrations.RunPython(backfill_customers, noop_reverse),
    ]
