"""Per-country branding presets (spec: "document branding presets per
country/company if different legal entities use different logos, addresses,
terms, or signature wording") — see CountryBrandingProfile's docstring for
why this is scoped by country alone, not by (document_type, country).
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role
from apps.locations.models import Location, LocationLevel

from .models import CountryBrandingProfile
from .pdf import file_to_data_uri
from .template_services import _validate_logo

# Fields a profile may override — blank/None on the profile always means
# "inherit from the document type's own template", never "render blank".
_OVERRIDE_TEXT_FIELDS = (
    "company_name",
    "company_address",
    "company_tax_id",
    "terms_text",
    "signature_left_label",
    "signature_right_label",
)


def eligible_countries():
    """Every Country-level Location — the picker for "add a profile"."""
    return Location.objects.filter(level=LocationLevel.COUNTRY).order_by("name")


def get_branding_profile(country):
    if country is None:
        return None
    return CountryBrandingProfile.objects.filter(country=country).select_related("country").first()


def list_branding_profiles():
    """{country_id: CountryBrandingProfile} for every country that has one —
    used by the branding list page to show which countries are already
    customized without an N+1 lookup per row.
    """
    return {
        profile.country_id: profile
        for profile in CountryBrandingProfile.objects.select_related("country")
    }


@transaction.atomic
def save_branding_profile(
    *,
    user,
    country,
    logo=None,
    remove_logo=False,
    company_name="",
    company_address="",
    company_tax_id="",
    terms_text="",
    signature_left_label="",
    signature_right_label="",
):
    """Creates or updates the one branding profile for `country`. Every text
    field is set exactly to what's passed (including blank — clearing a
    field is exactly how an Administrator reverts it to "inherit"), unlike
    apps.documents.template_services.update_template()'s "None means don't
    touch this field" convention: a branding profile has no separate
    draft/published state to preserve between partial saves, so the whole
    form is always the full, current intent.
    """
    require_role(user, ADMINISTRATOR)
    if country.level != LocationLevel.COUNTRY:
        raise ValidationError(f"'{country}' is a {country.get_level_display()}, not a Country.")
    if logo is not None:
        _validate_logo(logo)

    profile = CountryBrandingProfile.objects.filter(country=country).first()
    is_new = profile is None
    old_logo = (
        profile.logo if profile and profile.logo and (logo is not None or remove_logo) else None
    )
    if is_new:
        profile = CountryBrandingProfile(country=country)

    profile.company_name = company_name
    profile.company_address = company_address
    profile.company_tax_id = company_tax_id
    profile.terms_text = terms_text
    profile.signature_left_label = signature_left_label
    profile.signature_right_label = signature_right_label
    profile.updated_by = user
    if logo is not None:
        profile.logo = logo
    elif remove_logo and profile.logo:
        profile.logo = None
    profile.full_clean()
    profile.save()
    if old_logo:
        old_logo.delete(save=False)

    record_event(
        actor=user,
        event_type=(
            AuditEvent.EventType.RECORD_CREATED if is_new else AuditEvent.EventType.RECORD_UPDATED
        ),
        obj=profile,
        summary=f"{'Created' if is_new else 'Updated'} branding profile for {country}",
    )
    return profile


def apply_branding_override(context, *, country):
    """Merges `country`'s CountryBrandingProfile (if any) on top of an
    already-built render context (apps.documents.pdf.render_pdf()) — only
    the fields actually filled in on the profile override the document
    type's own template values; a blank profile field leaves the base
    template's value in place.
    """
    profile = get_branding_profile(country)
    if profile is None:
        return context
    context = dict(context)
    for field in _OVERRIDE_TEXT_FIELDS:
        value = getattr(profile, field)
        if value:
            context[field] = value
    if profile.logo:
        with profile.logo.open("rb") as f:
            data_uri = file_to_data_uri(f)
        if data_uri:
            context["logo_data_uri"] = data_uri
    return context
