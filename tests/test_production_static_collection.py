"""Exercise the same manifest post-processing used by the production Docker build."""

import json

from django.core.management import call_command


def test_production_static_collection(settings, tmp_path):
    settings.STATIC_ROOT = tmp_path / "collected"
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    call_command("collectstatic", interactive=False, verbosity=0)
    manifest = json.loads((settings.STATIC_ROOT / "staticfiles.json").read_text())
    paths = manifest["paths"]
    js = "vendor/grapesjs/grapes.min.js"
    source_map = js + ".map"
    assert (settings.STATIC_ROOT / paths[source_map]).is_file()
    content = (settings.STATIC_ROOT / paths[js]).read_text(encoding="utf-8")
    assert paths[source_map].rsplit("/", 1)[1] in content
