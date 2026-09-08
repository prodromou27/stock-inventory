from html.parser import HTMLParser
from xml.etree import ElementTree

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse


@pytest.mark.django_db
def test_find_stock_uses_bundled_search_icon(client, administrator):
    class ActionParser(HTMLParser):
        in_action = False
        icon = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "a":
                self.in_action = (
                    attrs.get("href") == reverse("inventory:asset_list") + "?in_storage=1"
                )
            if tag == "use" and self.in_action:
                self.icon = attrs.get("href")

        def handle_endtag(self, tag):
            if tag == "a":
                self.in_action = False

    client.force_login(administrator)
    parser = ActionParser()
    parser.feed(client.get(reverse("core:home")).content.decode())
    assert parser.icon.endswith("icons/sprite.svg#search")
    sprite = ElementTree.parse(finders.find("icons/sprite.svg"))
    assert sprite.find(".//{http://www.w3.org/2000/svg}symbol[@id='search']") is not None
