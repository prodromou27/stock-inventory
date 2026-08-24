from django.db import migrations

# Backs InventoryTransaction.transaction_number (apps/inventory/services/ledger.py's
# next_transaction_number()) — monotonic and unique but not gapless, per
# docs/architecture/02-data-model.md's numbering note.

CREATE_SEQUENCE_SQL = "CREATE SEQUENCE IF NOT EXISTS inventory_transaction_number_seq;"
DROP_SEQUENCE_SQL = "DROP SEQUENCE IF EXISTS inventory_transaction_number_seq;"


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SEQUENCE_SQL, reverse_sql=DROP_SEQUENCE_SQL),
    ]
