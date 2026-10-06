// Host tests for main/protocols/extra_headers.cc (core registry of host-scoped HTTP headers,
// used for the Cloudflare Access service token).
#include <catch2/catch_test_macros.hpp>

#include <string>
#include <utility>
#include <vector>

#include "extra_headers.h"

namespace {

std::vector<std::pair<std::string, std::string>> HeadersFor(const std::string& url) {
    std::vector<std::pair<std::string, std::string>> out;
    ExtraHeaders::GetInstance().ForEach(
        url, [&out](const std::string& name, const std::string& value) {
            out.emplace_back(name, value);
        });
    return out;
}

}  // namespace

TEST_CASE("HostFromUrl extracts the lower-case host name", "[extra_headers]") {
    CHECK(ExtraHeaders::HostFromUrl("wss://user@Jarvis.Example.com:443/xiaozhi/v1/") ==
          "jarvis.example.com");
    CHECK(ExtraHeaders::HostFromUrl("https://jarvis.example.com/xiaozhi/ota/") ==
          "jarvis.example.com");
    CHECK(ExtraHeaders::HostFromUrl("http://192.168.1.27:8003/xiaozhi/ota/") == "192.168.1.27");
    CHECK(ExtraHeaders::HostFromUrl("https://[::1]:8443/x") == "[::1]");
    CHECK(ExtraHeaders::HostFromUrl("https://h.example?x=1") == "h.example");
    CHECK(ExtraHeaders::HostFromUrl("") == "");
}

TEST_CASE("IsTlsUrl accepts https and wss only", "[extra_headers]") {
    CHECK(ExtraHeaders::IsTlsUrl("https://jarvis.example.com/"));
    CHECK(ExtraHeaders::IsTlsUrl("wss://jarvis.example.com/xiaozhi/v1/"));
    CHECK(ExtraHeaders::IsTlsUrl("HTTPS://jarvis.example.com/"));
    CHECK_FALSE(ExtraHeaders::IsTlsUrl("http://jarvis.example.com/"));
    CHECK_FALSE(ExtraHeaders::IsTlsUrl("ws://jarvis.example.com/"));
    CHECK_FALSE(ExtraHeaders::IsTlsUrl("jarvis.example.com/x"));
    CHECK_FALSE(ExtraHeaders::IsTlsUrl("https:/jarvis.example.com"));
    CHECK_FALSE(ExtraHeaders::IsTlsUrl(""));
}

TEST_CASE("Registered headers go to TLS URLs of their host only", "[extra_headers]") {
    auto& registry = ExtraHeaders::GetInstance();
    REQUIRE(registry.Set("Jarvis.Example.com", "CF-Access-Client-Id", "id.access"));
    REQUIRE(registry.Set("jarvis.example.com", "CF-Access-Client-Secret", "cfast_secret"));
    CHECK(registry.HasHost("jarvis.example.com"));

    const auto ota = HeadersFor("https://jarvis.example.com/xiaozhi/ota/");
    REQUIRE(ota.size() == 2);
    CHECK(ota[0] == std::make_pair(std::string("CF-Access-Client-Id"), std::string("id.access")));
    CHECK(ota[1].second == "cfast_secret");
    CHECK(HeadersFor("wss://JARVIS.example.com:443/xiaozhi/v1/").size() == 2);

    // A runtime-supplied plain-text URL to the same host (e.g. a firmware URL handed to
    // self.upgrade_firmware) must never carry the secret.
    CHECK(HeadersFor("http://jarvis.example.com/x.bin").empty());
    CHECK(HeadersFor("ws://jarvis.example.com/xiaozhi/v1/").empty());
    CHECK(HeadersFor("http://jarvis.example.com:443/x").empty());
    // Other hosts never get them.
    CHECK(HeadersFor("https://evil.example.com/jarvis.example.com").empty());
    CHECK(HeadersFor("https://jarvis.example.com.evil.net/").empty());

    // Replacing a value keeps one header; values with line breaks are rejected.
    REQUIRE(registry.Set("jarvis.example.com", "cf-access-client-id", "id2.access"));
    CHECK(HeadersFor("https://jarvis.example.com/").size() == 2);
    CHECK_FALSE(registry.Set("jarvis.example.com", "X-Test", "a\r\nInjected: 1"));

    registry.ClearHost("jarvis.example.com");
    CHECK_FALSE(registry.HasHost("jarvis.example.com"));
    CHECK(HeadersFor("https://jarvis.example.com/").empty());
}
