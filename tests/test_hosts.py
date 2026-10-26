import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "event-proxy"))
import hosts


class DefaultHosts(unittest.TestCase):
    def test_matches(self):
        for host in [
            "app-measurement.com", "region1.app-analytics-services.com",
            "www.google-analytics.com", "region1.google-analytics.com", "region1.analytics.google.com",
            "sgtm.example.com", "sst.example.com", "gtmss.example.com", "data.example.com",
            "track.example.com", "tracking.example.dk", "sgtm-eu.example.com", "a.b.sst.example.com",
            "googleanalytics.example.com", "SGTM.Example.COM",
        ]:
            self.assertTrue(hosts.matches(host), host)

    def test_other_hosts_are_left_alone(self):
        for host in [
            "example.com", "www.example.com", "media.example.com", "api.itunes.apple.com",
            "metadata.example.com", "assets.example.com", "notgoogle-analytics.com.evil.test",
            "example.com.sgtm", "gtm.example.com",
        ]:
            self.assertFalse(hosts.matches(host), host)

    def test_firebase_hosts(self):
        self.assertTrue(hosts.is_firebase("region1.app-analytics-services.com"))
        self.assertTrue(hosts.is_firebase("app-measurement.com"))
        self.assertFalse(hosts.is_firebase("sgtm.example.com"))
        self.assertFalse(hosts.is_firebase("www.google-analytics.com"))


class CustomDomains(unittest.TestCase):
    def test_plain_domain_covers_subdomains(self):
        self.assertTrue(hosts.matches("example.com", ["example.com"]))
        self.assertTrue(hosts.matches("shop.example.com", ["example.com"]))
        self.assertFalse(hosts.matches("notexample.com", ["example.com"]))
        self.assertFalse(hosts.matches("example.com", []))

    def test_glob_matches_whole_host(self):
        self.assertTrue(hosts.matches("api7.foo.net", ["api*.foo.*"]))
        self.assertTrue(hosts.matches("api.a.b.foo.org", ["api*.foo.*"]))
        self.assertFalse(hosts.matches("x.api.foo.net", ["api*.foo.*"]))

    def test_domain_text_is_split_on_spaces_and_commas(self):
        self.assertEqual(hosts.split_domains("a.com, b.com  c.com"), ["a.com", "b.com", "c.com"])
        self.assertEqual(hosts.split_domains(None), [])

    def test_special_characters_are_literal(self):
        self.assertFalse(hosts.matches("exampleXcom", ["example.com"]))


class AllowHostsRegex(unittest.TestCase):
    def test_form_mitmproxy_uses(self):
        rx = hosts.allow_hosts_regex(["custom.test"])
        self.assertTrue(re.search(rx, "sgtm.example.com:443"))
        self.assertTrue(re.search(rx, "a.custom.test:443"))
        self.assertTrue(re.search(rx, "sgtm.example.com:8443"))  # any port
        self.assertFalse(re.search(rx, "sgtm.example.com"))
        self.assertFalse(re.search(rx, "media.example.com:443"))


if __name__ == "__main__":
    unittest.main()
