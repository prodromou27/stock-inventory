from django.db import migrations

# Backs GeneratedDocument.document_number (apps/documents/services.py's
# next_document_number()) — same numbering scheme as
# inventory_transaction_number_seq (docs/architecture/02-data-model.md).

CREATE_SEQUENCE_SQL = "CREATE SEQUENCE IF NOT EXISTS document_number_seq;"
DROP_SEQUENCE_SQL = "DROP SEQUENCE IF EXISTS document_number_seq;"


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SEQUENCE_SQL, reverse_sql=DROP_SEQUENCE_SQL),
    ]
