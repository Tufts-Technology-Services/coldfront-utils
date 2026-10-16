import json
from unittest import mock

import pytest
from django.test import override_settings
from ldap3 import get_config_parameter, set_config_parameter

from coldfront_utils.util.ad_search import ADSearch, exclude_attributes_from_schema_check

AD_SETTINGS = {
    "LDAP_USER_SEARCH_SERVER_URI": "ldaps://ad.example.edu",
    "LDAP_USER_SEARCH_BASE": "dc=example,dc=edu",
    "LDAP_USER_SEARCH_ATTRIBUTE_MAP": {
        "username": "sAMAccountName",
        "last_name": "sn",
        "first_name": "givenName",
        "email": "mail",
    },
}


def ad_entry(**attributes):
    """An ldap3 Entry as ADSearch reads it: only through entry_to_json()."""
    return mock.Mock(entry_to_json=mock.Mock(return_value=json.dumps({"attributes": attributes})))


@pytest.fixture(autouse=True)
def schema_check_exclusions():
    """Restore ldap3's process-wide ATTRIBUTES_EXCLUDED_FROM_CHECK after each test."""
    original = list(get_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK"))
    yield
    set_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK", original)


def excluded_from_schema_check():
    return get_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK")


@pytest.fixture
def connection():
    """ldap3's Connection, as LDAPUserSearch constructs and binds it. Set `.entries` for results."""
    with (
        override_settings(**AD_SETTINGS),
        mock.patch("coldfront.plugins.ldap_user_search.utils.Connection") as connection_class,
    ):
        connection_class.return_value.entries = []
        yield connection_class.return_value


@pytest.fixture
def ad(connection):
    return ADSearch(user_search_string=None, search_by="all_fields")


def searched_filter(connection):
    return connection.search.call_args.kwargs["search_filter"]


class TestGetAdUser:
    def test_returns_the_attributes_of_the_matching_entry(self, ad, connection):
        connection.entries = [ad_entry(sAMAccountName="jdoe01", uidNumber=51234)]

        assert ad.get_ad_user("jdoe01") == {"sAMAccountName": "jdoe01", "uidNumber": 51234}
        assert searched_filter(connection) == "(&(sAMAccountName=jdoe01)(objectclass=person))"
        assert connection.search.call_args.kwargs["search_base"] == "dc=example,dc=edu"

    def test_returns_none_when_there_is_no_match(self, ad, connection):
        assert ad.get_ad_user("nobody01") is None

    def test_filter_metacharacters_in_the_username_are_escaped(self, ad, connection):
        ad.get_ad_user("jdoe01)(sAMAccountName=*")

        assert searched_filter(connection) == (
            r"(&(sAMAccountName=jdoe01\29\28sAMAccountName=\2a)(objectclass=person))"
        )


class TestGetAdGroup:
    def test_searches_groups_by_account_name(self, ad, connection):
        connection.entries = [ad_entry(sAMAccountName="lab01", gidNumber=6100)]

        assert ad.get_ad_group("lab01") == {"sAMAccountName": "lab01", "gidNumber": 6100}
        assert searched_filter(connection) == "(&(sAMAccountName=lab01)(objectclass=group))"


class TestSearchAUser:
    def test_username_only_returns_at_most_one_user(self, ad, connection):
        ad.search_a_user("jdoe01", search_by="username_only")

        assert searched_filter(connection) == "(&(sAMAccountName=jdoe01)(objectclass=person))"
        assert connection.search.call_args.kwargs["size_limit"] == 1

    def test_all_fields_escapes_wildcards_in_the_search_string(self, ad, connection):
        ad.search_a_user("j*e", search_by="all_fields")

        assert r"(sAMAccountName=*j\2ae*)" in searched_filter(connection)


class TestSchemaCheckExclusions:
    def test_searched_attributes_are_excluded(self, ad, connection):
        ad.get_ad_user("jdoe01")

        assert {"sAMAccountName", "uidNumber", "gidNumber"} <= set(excluded_from_schema_check())

    def test_repeated_searches_do_not_grow_the_list(self, ad, connection):
        ad.search_a_user("jdoe01", search_by="username_only")
        ad.get_ad_user("jdoe01")
        ad.get_ad_group("lab01")
        settled = list(excluded_from_schema_check())

        for _ in range(3):
            ad.search_a_user("jdoe01", search_by="username_only")
            ad.get_ad_user("jdoe01")
            ad.get_ad_group("lab01")

        assert excluded_from_schema_check() == settled

    def test_attributes_already_excluded_in_another_case_are_not_added(self):
        set_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK", ["MAIL"])

        exclude_attributes_from_schema_check(["mail", "sn", "SN"])

        assert excluded_from_schema_check() == ["MAIL", "sn"]
