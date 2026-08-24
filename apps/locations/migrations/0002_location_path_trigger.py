from django.db import migrations

# Defense-in-depth alongside apps/locations/services.py's pre-write validation
# (docs/architecture/02-data-model.md): computes `path` from the parent chain
# and rejects a level/parent combination that skips or repeats a level, even
# if something bypasses the service layer (a raw script, a future API).
#
# UUID hyphens aren't valid ltree label characters, so each node's path label
# is its id with hyphens stripped (32 hex chars).
#
# Re-parenting (changing an existing row's parent/level) is intentionally not
# supported — the trigger only fires BEFORE INSERT.

SET_PATH_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION locations_location_set_path() RETURNS trigger AS $$
DECLARE
    parent_path ltree;
    parent_level varchar;
    expected_child_level varchar;
BEGIN
    IF NEW.parent_id IS NULL THEN
        IF NEW.level <> 'country' THEN
            RAISE EXCEPTION 'Only a country-level location may have no parent';
        END IF;
        NEW.path := text2ltree(replace(NEW.id::text, '-', ''));
    ELSE
        SELECT path, level INTO parent_path, parent_level
        FROM locations_location WHERE id = NEW.parent_id;

        IF parent_path IS NULL THEN
            RAISE EXCEPTION 'Parent location % does not exist', NEW.parent_id;
        END IF;

        expected_child_level := CASE parent_level
            WHEN 'country' THEN 'site'
            WHEN 'site' THEN 'floor'
            WHEN 'floor' THEN 'storage_room'
            WHEN 'storage_room' THEN 'rack_cabinet'
            WHEN 'rack_cabinet' THEN 'shelf_bin'
            ELSE NULL
        END;

        IF expected_child_level IS NULL OR NEW.level <> expected_child_level THEN
            RAISE EXCEPTION 'A % may only be created under a %, but parent % is a %',
                NEW.level, expected_child_level, NEW.parent_id, parent_level;
        END IF;

        NEW.path := parent_path || text2ltree(replace(NEW.id::text, '-', ''));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS locations_location_set_path() CASCADE;"

CREATE_TRIGGER_SQL = """
CREATE TRIGGER locations_location_set_path_trigger
BEFORE INSERT ON locations_location
FOR EACH ROW EXECUTE FUNCTION locations_location_set_path();
"""

DROP_TRIGGER_SQL = (
    "DROP TRIGGER IF EXISTS locations_location_set_path_trigger ON locations_location;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=SET_PATH_FUNCTION_SQL, reverse_sql=DROP_FUNCTION_SQL),
        migrations.RunSQL(sql=CREATE_TRIGGER_SQL, reverse_sql=DROP_TRIGGER_SQL),
    ]
