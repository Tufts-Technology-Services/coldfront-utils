"""The API extension registry: what register_api_extension accepts and refuses, and the lookups."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from coldfront_utils.api_extensions import (
    AllocationExtension,
    ApiExtension,
    ExtensionBadRequest,
    ExtensionNotFound,
    ExtensionUnavailable,
    PersonExtension,
    ProjectExtension,
    extensions,
    find_extension,
    register_api_extension,
    registered_extensions,
)


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    """An empty registry, so each test registers its own extensions."""
    monkeypatch.setattr(extensions, "_registry", {})


def make_extension_class(base=PersonExtension, class_name="SampleExtension", **attributes):
    """A complete extension class of base's kind, with `attributes` replacing its defaults."""

    def allows(self, target, caller):
        return True

    def summary(self, target, caller, params):
        return {"extension": self.name, "version": self.version}

    body = {"name": "sample", "description": "A sample extension", "allows": allows, "summary": summary}
    body.update(attributes)
    return type(class_name, (base,), body)


@pytest.mark.parametrize(
    "kind, base", [("person", PersonExtension), ("project", ProjectExtension), ("allocation", AllocationExtension)]
)
def test_a_registered_extension_is_found_under_its_kind_and_name(kind, base):
    extension_class = make_extension_class(base, name="storage")

    returned = register_api_extension(extension_class)

    assert returned is extension_class
    assert find_extension(kind, "storage") is extension_class


def test_register_works_as_a_decorator():
    @register_api_extension
    class Quotas(PersonExtension):
        name = "quotas"
        description = "Quotas"

        def allows(self, target, caller):
            return True

        def summary(self, target, caller, params):
            return {}

    assert find_extension("person", "quotas") is Quotas


def test_an_unregistered_name_or_unknown_kind_is_not_found():
    register_api_extension(make_extension_class(PersonExtension, name="storage"))

    assert find_extension("person", "usage") is None
    assert find_extension("project", "storage") is None
    assert find_extension("group", "storage") is None


def test_the_same_name_can_be_used_by_different_kinds():
    person_usage = make_extension_class(PersonExtension, name="usage")
    allocation_usage = make_extension_class(AllocationExtension, name="usage")

    register_api_extension(person_usage)
    register_api_extension(allocation_usage)

    assert find_extension("person", "usage") is person_usage
    assert find_extension("allocation", "usage") is allocation_usage


def test_registering_the_same_class_again_is_allowed():
    extension_class = make_extension_class(name="storage")

    register_api_extension(extension_class)
    register_api_extension(extension_class)

    assert find_extension("person", "storage") is extension_class


def test_a_second_class_with_the_same_kind_and_name_is_refused():
    register_api_extension(make_extension_class(name="storage", class_name="StorageAreas"))

    with pytest.raises(ImproperlyConfigured, match="StorageAreas"):
        register_api_extension(make_extension_class(name="storage", class_name="OtherStorage"))


@pytest.mark.parametrize("name", ["", None, "Storage", "1st", "-storage", "home_quota", "a/b", "a.b", "storage "])
def test_a_name_that_is_not_a_url_segment_of_lowercase_letters_digits_and_hyphens_is_refused(name):
    with pytest.raises(ImproperlyConfigured, match="name"):
        register_api_extension(make_extension_class(name=name))


@pytest.mark.parametrize("name", ["storage", "storage-v2", "a1"])
def test_names_of_lowercase_letters_digits_and_hyphens_are_accepted(name):
    register_api_extension(make_extension_class(name=name))

    assert find_extension("person", name) is not None


def test_the_base_class_itself_is_refused():
    with pytest.raises(ImproperlyConfigured, match="PersonExtension, ProjectExtension or AllocationExtension"):
        register_api_extension(make_extension_class(ApiExtension))


def test_a_class_that_is_no_extension_is_refused():
    with pytest.raises(ImproperlyConfigured, match="PersonExtension, ProjectExtension or AllocationExtension"):
        register_api_extension(type("NotAnExtension", (), {"name": "sample"}))


def test_an_instance_rather_than_a_class_is_refused():
    with pytest.raises(ImproperlyConfigured, match="class"):
        register_api_extension(make_extension_class()())


def test_a_class_of_two_kinds_is_refused():
    both = type("Both", (PersonExtension, ProjectExtension), {})

    with pytest.raises(ImproperlyConfigured, match="one"):
        register_api_extension(make_extension_class(both))


@pytest.mark.parametrize("method", ["allows", "summary"])
def test_a_class_that_does_not_implement_a_required_method_is_refused(method):
    extension_class = make_extension_class()
    delattr(extension_class, method)

    with pytest.raises(ImproperlyConfigured, match=method):
        register_api_extension(extension_class)


@pytest.mark.parametrize("description", ["", None])
def test_an_empty_description_is_refused(description):
    with pytest.raises(ImproperlyConfigured, match="description"):
        register_api_extension(make_extension_class(description=description))


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, True, None])
def test_a_version_that_is_not_a_positive_integer_is_refused(version):
    with pytest.raises(ImproperlyConfigured, match="version"):
        register_api_extension(make_extension_class(version=version))


def test_a_newer_version_registers_under_its_own_name_beside_the_deprecated_one():
    old = make_extension_class(name="storage", deprecated=True)
    new = make_extension_class(name="storage-v2", version=2)

    register_api_extension(old)
    register_api_extension(new)

    assert registered_extensions()["person"] == [old, new]


def test_registered_extensions_lists_every_kind_with_each_list_sorted_by_name():
    scratch = make_extension_class(PersonExtension, name="scratch")
    home = make_extension_class(PersonExtension, name="home")
    usage = make_extension_class(AllocationExtension, name="usage")
    for extension_class in (scratch, usage, home):
        register_api_extension(extension_class)

    assert registered_extensions() == {"person": [home, scratch], "project": [], "allocation": [usage]}


def test_registered_extensions_with_nothing_registered_lists_every_kind_empty():
    assert registered_extensions() == {"person": [], "project": [], "allocation": []}


def test_by_default_an_extension_has_no_items():
    extension = make_extension_class()()

    with pytest.raises(ExtensionNotFound):
        extension.item(target=None, key="home", caller=None, params={})


def test_the_defaults_are_version_1_and_not_deprecated():
    extension_class = make_extension_class()

    assert extension_class.version == 1
    assert extension_class.deprecated is False


def test_a_summary_that_does_not_take_params_is_refused():
    def summary(self, target, caller):
        return {}

    with pytest.raises(ImproperlyConfigured, match="params"):
        register_api_extension(make_extension_class(summary=summary))


def test_an_item_that_does_not_take_params_is_refused():
    def item(self, target, key, caller):
        return {}

    with pytest.raises(ImproperlyConfigured, match="params"):
        register_api_extension(make_extension_class(item=item))


def test_methods_taking_params_by_keyword_or_as_keyword_arguments_are_accepted():
    def summary(self, target, caller, *, params):
        return {}

    def item(self, target, key, caller, **kwargs):
        return {}

    register_api_extension(make_extension_class(summary=summary, item=item))

    assert find_extension("person", "sample") is not None


def test_the_exceptions_carry_a_message_for_the_caller():
    assert str(ExtensionBadRequest("Unknown status: Gone.")) == "Unknown status: Gone."
    assert str(ExtensionUnavailable("Storage is unavailable.")) == "Storage is unavailable."
