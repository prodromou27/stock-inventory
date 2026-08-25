import os

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditEvent
from apps.settings.models import SystemSettings
from apps.settings.services import update_certificate, update_system_settings

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000155e75dd8000000004"
    "9454e44ae426082"
)

# A throwaway, self-signed, no-real-world-value 10-year certificate — CN
# doesn't matter, only that cert+key structurally match (apps.settings.
# services._validate_cert_key_pair uses the stdlib ssl module to check that).
VALID_CERT_PEM = b"""-----BEGIN CERTIFICATE-----
MIIDFzCCAf+gAwIBAgIUV0PysLiwwI32FTtq7srqHvPNMT0wDQYJKoZIhvcNAQEL
BQAwGzEZMBcGA1UEAwwQdGVzdC5leGFtcGxlLmNvbTAeFw0yNjA4MjUxNDMwMjJa
Fw0zNjA4MjIxNDMwMjJaMBsxGTAXBgNVBAMMEHRlc3QuZXhhbXBsZS5jb20wggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDXyVr1HuSoTVhwpNZ33VW2yjr8
rYd66Ujttu4wQkp7u7X3Zd1v/XZ7zkYiU4AOKIzntk3z7R29qjHg9SbU2lc6TteF
81/v/qjehHY2FNyBjwjHtKzajRxrOB/POeVUb0BP8X9ETnoWAP0Idkip0XdY/NhJ
8h5agj7w7cY9U3gO61sMiYV2UHReF5U9Y0tquStysJQ+Hyw6bqvs9t0GPB4wfHU5
71qASxpj7DH/YVg+vPvDQgEqpmzvQ2JYQaEKQ03/YvtMZLcG/WjUesm+FACR7237
jbAbGNQR9rzwe+Iu7BNESWX3OWRUgowyqYmqMzcoYyf21PkWIP7/Cak+oUOLAgMB
AAGjUzBRMB0GA1UdDgQWBBRPGD+mGftJRItusRg/w3ENvn3MBTAfBgNVHSMEGDAW
gBRPGD+mGftJRItusRg/w3ENvn3MBTAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3
DQEBCwUAA4IBAQAfEB92ss0YtG4dShVRFSkLQu4Y+O5GELLSxhPeLXOYoQfBx98N
sLXBTPjvVJJVl2//8RhQz19X8lzfO/nvmhK0CP3bCTx3oU7Xdshrl3htbBPwTs+p
LRL870OYj8drcN41Sy6aMvaZ81CV/tY6oNRB6t91GMRdB4X7RYSiqUfhGAMVz0oL
Wf01ASbN1tGHw3DN7bot4O5aHRApKekEDcJDXO/VgR8ce9iAujnqBNzLvr4kmLU+
SQKfLB+77oJHx1oakCGhGpiGWNEokWrPI6NMjRfbHTi+KRH/wGaZgABqgpgt+bQk
d5IUIKsz7JmrDLe6b5fqvoEQ8YDJN/2aYiLz
-----END CERTIFICATE-----
"""

VALID_KEY_PEM = b"""-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDXyVr1HuSoTVhw
pNZ33VW2yjr8rYd66Ujttu4wQkp7u7X3Zd1v/XZ7zkYiU4AOKIzntk3z7R29qjHg
9SbU2lc6TteF81/v/qjehHY2FNyBjwjHtKzajRxrOB/POeVUb0BP8X9ETnoWAP0I
dkip0XdY/NhJ8h5agj7w7cY9U3gO61sMiYV2UHReF5U9Y0tquStysJQ+Hyw6bqvs
9t0GPB4wfHU571qASxpj7DH/YVg+vPvDQgEqpmzvQ2JYQaEKQ03/YvtMZLcG/WjU
esm+FACR7237jbAbGNQR9rzwe+Iu7BNESWX3OWRUgowyqYmqMzcoYyf21PkWIP7/
Cak+oUOLAgMBAAECggEAB0725AXHgXpv0WOJLrFyEr58taF8TN8T15ac20jo1Dys
/WkXe+XeGnQHuhKRQWmBhDjky6svQ6+SVZKCsy4woB/wJXBIbTd3KWTbysR1ch88
Zo8pqj6h73EM2sGU5oGHcG23KBgZrcnGHYBBa8cAFM/Tf4Wnc4dPxxc2jKMuwMqL
O8L2tAkkX3O1E+rGKyAsZa3Xz1ulP54YWxVBvGwM0Doc7a0ATPN+swQbvHIcrTGt
PZxmP7MQHGyhQgkuJ8wsZZtlX7+JKqKu40ItNC4BbRnKAfN2oN2kbWwVoYBqNYgk
13Q/VGPZRFjbl1rig3xLJIjpIh51C5qfxAn4evZhCQKBgQD/nckM8eKwgNJjIVFy
O5HPrXx0nFcRxHovKVhuZtaQxrvRyxePapRaGdB4jvHa+ruipd1w1KhcEsqxjFeN
jWWvhSZPFBxmUMA3stKx4PaTtd9WkUWI005M0siRMsedCDhD6YhW2HgN+XsLzbMI
vacCfp1j6mWLqP9T6wnzTIHCzQKBgQDYHEQqXDvk70qX/d6hfXavevqEI/CNuYAU
1DHPRR2sSlBJWBeTMUN1dOFlB1i3W6VqkuGjlcYc6jnVIF07oEX5AB+mSGaoWEx4
h3MQU3XkcoI4Rsguxwx2+jqykvzFVJBl8sptCu+WOi79qNbDiyb8je7JHqqmqaRz
htLdA0kPtwKBgQCDZf/IyLrIEqCT1rfAagDNahf09b0IZCCPB8juj6yypYY9FRJa
ZeY23tg8cPbAo2068dqAOsEE+5/4XvVOzaW88Uw9EFB9A/ubZjBKwiLe0XoqTOka
qcyxTxVNFnZoMaaCupScWBB21o07BxWGY79rV9zeVMq4XTpLUCJAUE7P7QKBgH5/
JSNKc8CQlLAc6KjMQKF8sZCRXOgIMcF/Z1x0j2be5NnZl4sP5fWloZ06TKKfIVcd
fLf9Hkakj4+B7zDMosiaWuxBKt3VOYW/ewmOYM6EfFamj9xZpKEr3RnT0eNLmW4j
THvBT/Y2PnU50+QH2p0wExpkOe1uFRWOUHUPzD9HAoGAAUU11jY9ONT/aZU/uJFL
Bxc/MC1OZ8aeJWki2rx3DnS6da4wPnLMl8q3iVrkrZKGGctE7d0RNhevmxRsiZb2
JvfvlANODy7AL0kgyE94+sSj/ynBdaf+2S8iMxe6kPj9Wx+NDDeo8fvhfSJNql4X
JhDH8bOOCmOBaTMmNLcF7og=
-----END PRIVATE KEY-----
"""


def _png_upload(name="logo.png"):
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


@pytest.mark.django_db
class TestUpdateSystemSettings:
    def test_saves_site_name_and_allowed_hosts(self, administrator):
        settings_obj = update_system_settings(
            user=administrator,
            site_name="Acme Inventory",
            allowed_hosts_override="acme.example.com, warehouse.example.com",
        )
        assert settings_obj.site_name == "Acme Inventory"
        assert settings_obj.allowed_hosts_override == "acme.example.com, warehouse.example.com"
        assert settings_obj.updated_by == administrator
        assert SystemSettings.objects.count() == 1

    def test_blank_site_name_falls_back_to_default(self, administrator):
        settings_obj = update_system_settings(
            user=administrator, site_name="  ", allowed_hosts_override=""
        )
        assert settings_obj.site_name == "Stock Inventory"

    def test_requires_administrator(self, stock_manager):
        with pytest.raises(PermissionDenied):
            update_system_settings(user=stock_manager, site_name="Nope", allowed_hosts_override="")

    def test_saves_a_logo(self, administrator):
        settings_obj = update_system_settings(
            user=administrator,
            site_name="Acme",
            allowed_hosts_override="",
            logo=_png_upload(),
        )
        assert settings_obj.logo.name

    def test_rejects_a_non_image_logo(self, administrator):
        bad_file = SimpleUploadedFile("logo.txt", b"not an image", content_type="text/plain")
        with pytest.raises(ValidationError, match="PNG or JPEG"):
            update_system_settings(
                user=administrator, site_name="Acme", allowed_hosts_override="", logo=bad_file
            )

    def test_remove_logo_clears_it(self, administrator):
        update_system_settings(
            user=administrator, site_name="Acme", allowed_hosts_override="", logo=_png_upload()
        )
        settings_obj = update_system_settings(
            user=administrator, site_name="Acme", allowed_hosts_override="", remove_logo=True
        )
        assert not settings_obj.logo

    def test_records_an_audit_event(self, administrator):
        update_system_settings(
            user=administrator, site_name="Acme", allowed_hosts_override="example.com"
        )
        event = AuditEvent.objects.filter(summary="Updated system settings").first()
        assert event is not None
        assert event.actor == administrator


@pytest.mark.django_db
class TestUpdateCertificate:
    def test_saves_valid_cert_and_key(self, administrator, certs_dir):

        update_certificate(
            user=administrator,
            cert_file=SimpleUploadedFile("fullchain.pem", VALID_CERT_PEM),
            key_file=SimpleUploadedFile("privkey.pem", VALID_KEY_PEM),
        )

        with open(os.path.join(certs_dir, "fullchain.pem"), "rb") as f:
            assert f.read() == VALID_CERT_PEM
        with open(os.path.join(certs_dir, "privkey.pem"), "rb") as f:
            assert f.read() == VALID_KEY_PEM

    def test_rejects_mismatched_cert_and_key(self, administrator, certs_dir):

        wrong_key = VALID_KEY_PEM.replace(b"A", b"B", 1)  # corrupt the PEM body

        with pytest.raises(ValidationError, match="not a valid, matching pair"):
            update_certificate(
                user=administrator,
                cert_file=SimpleUploadedFile("fullchain.pem", VALID_CERT_PEM),
                key_file=SimpleUploadedFile("privkey.pem", wrong_key),
            )
        assert not os.path.exists(os.path.join(certs_dir, "fullchain.pem"))

    def test_requires_administrator(self, stock_manager, certs_dir):
        with pytest.raises(PermissionDenied):
            update_certificate(
                user=stock_manager,
                cert_file=SimpleUploadedFile("fullchain.pem", VALID_CERT_PEM),
                key_file=SimpleUploadedFile("privkey.pem", VALID_KEY_PEM),
            )

    def test_records_an_audit_event(self, administrator, certs_dir):
        update_certificate(
            user=administrator,
            cert_file=SimpleUploadedFile("fullchain.pem", VALID_CERT_PEM),
            key_file=SimpleUploadedFile("privkey.pem", VALID_KEY_PEM),
        )
        assert AuditEvent.objects.filter(
            summary__icontains="Uploaded a new TLS certificate"
        ).exists()
