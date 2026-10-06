// Host tests for the optional access token of the local MCP WebSocket (local_mcp_policy.h,
// contract D27): query parsing of ?token=, constant-time comparison, handshake decision.
#include <catch2/catch_test_macros.hpp>

#include <string>
#include <string_view>

#include "local_mcp_policy.h"

using local_mcp::CheckToken;
using local_mcp::ConstantTimeEquals;
using local_mcp::FindQueryValue;
using local_mcp::IsValidToken;
using local_mcp::QueryValue;
using local_mcp::TokenCheck;

namespace {

// 43 characters like tools/provision_secrets.py --generate-mcp-token (plus '.' and '~').
constexpr std::string_view kToken = "Qm9-x_Z3.kL~p0aB7cD1eF2gH4iJ5jK6lM8nN0oP2qr";
static_assert(kToken.size() == 43);

std::string Target(std::string_view query) {
    return "/ws?" + std::string(query);
}

// Every byte of `s` as %XX (what an over-eager client might send).
std::string PercentEncodeAll(std::string_view s) {
    static constexpr char kHex[] = "0123456789ABCDEF";
    std::string out;
    for (unsigned char c : s) {
        out += '%';
        out += kHex[c >> 4];
        out += kHex[c & 0x0F];
    }
    return out;
}

struct Lookup {
    QueryValue result;
    std::string value;
};

Lookup Find(std::string_view target, std::string_view key = "token") {
    Lookup lookup{QueryValue::kMissing, "sentinel"};
    lookup.result = FindQueryValue(target, key, lookup.value);
    return lookup;
}

}  // namespace

TEST_CASE("token query parameter: missing", "[local_mcp][token]") {
    for (const char* target : {"/ws", "/ws?", "/ws?foo=1", "/ws?foo=1&bar=2", "/ws?&&",
                               "/ws?Token=abc", "/ws?TOKEN=abc", "/ws?tokenx=abc",
                               "/ws?xtoken=abc", "/ws?to%6Ben=abc", "/ws#token=abc",
                               "/ws#x?token=abc", "/ws;token=abc", "/ws/token=abc"}) {
        CAPTURE(target);
        const Lookup lookup = Find(target);
        CHECK(lookup.result == QueryValue::kMissing);
        CHECK(lookup.value.empty());
    }
}

TEST_CASE("token query parameter: present, empty and extra parameters", "[local_mcp][token]") {
    CHECK(Find("/ws?token=abc").result == QueryValue::kFound);
    CHECK(Find("/ws?token=abc").value == "abc");
    // Empty value, with or without '='.
    CHECK(Find("/ws?token=").result == QueryValue::kFound);
    CHECK(Find("/ws?token=").value.empty());
    CHECK(Find("/ws?token").result == QueryValue::kFound);
    CHECK(Find("/ws?token").value.empty());
    // Position among other parameters, empty segments, fragment.
    CHECK(Find("/ws?foo=1&token=abc&bar=2").value == "abc");
    CHECK(Find("/ws?token=abc&foo=1").value == "abc");
    CHECK(Find("/ws?foo&token=abc").value == "abc");
    CHECK(Find("/ws?&&token=abc&").value == "abc");
    CHECK(Find("/ws?token=abc#frag").value == "abc");
    CHECK(Find("/ws?token=abc#&token=def").value == "abc");
    // '=' inside the value belongs to the value.
    CHECK(Find("/ws?token=a=b").value == "a=b");
    // A broken escape in another parameter does not matter.
    CHECK(Find("/ws?foo=%zz&token=abc").value == "abc");
    // Absolute-form request target.
    CHECK(Find("http://192.168.1.50:8080/ws?token=abc").value == "abc");
    // Other keys can be looked up the same way.
    CHECK(Find("/ws?a=1&b=2", "b").value == "2");
}

TEST_CASE("token query parameter: URL-encoded values", "[local_mcp][token]") {
    CHECK(Find("/ws?token=a%2Db%7Ec%2e").value == "a-b~c.");
    CHECK(Find("/ws?token=%41%62%63").value == "Abc");
    CHECK(Find("/ws?token=a+b").value == "a b");
    CHECK(Find("/ws?token=a%2Bb").value == "a+b");
    CHECK(Find("/ws?token=a%26b").value == "a&b");
    CHECK(Find("/ws?token=a%23b").value == "a#b");
    const Lookup nul = Find("/ws?token=%00ab");
    CHECK(nul.result == QueryValue::kFound);
    CHECK(nul.value == std::string("\0ab", 3));
    // Broken escapes make the parameter malformed.
    for (const char* target : {"/ws?token=%", "/ws?token=abc%", "/ws?token=abc%4",
                               "/ws?token=%4g", "/ws?token=%zz", "/ws?token=%%41"}) {
        CAPTURE(target);
        const Lookup lookup = Find(target);
        CHECK(lookup.result == QueryValue::kMalformed);
        CHECK(lookup.value.empty());
    }
}

TEST_CASE("token query parameter: repeated key is malformed", "[local_mcp][token]") {
    for (const char* target : {"/ws?token=abc&token=abc", "/ws?token=abc&token=def",
                               "/ws?token=&token=abc", "/ws?token=abc&token",
                               "/ws?token&token"}) {
        CAPTURE(target);
        const Lookup lookup = Find(target);
        CHECK(lookup.result == QueryValue::kMalformed);
        CHECK(lookup.value.empty());
    }
}

TEST_CASE("constant-time comparison", "[local_mcp][token]") {
    CHECK(ConstantTimeEquals(kToken, kToken));
    CHECK(ConstantTimeEquals(kToken, std::string(kToken)));
    CHECK(ConstantTimeEquals("", ""));

    std::string last = std::string(kToken);
    last.back() = 'x';
    std::string first = std::string(kToken);
    first.front() = 'x';
    CHECK_FALSE(ConstantTimeEquals(kToken, last));
    CHECK_FALSE(ConstantTimeEquals(kToken, first));
    // Wrong length: prefix, extension, empty, both directions.
    CHECK_FALSE(ConstantTimeEquals(kToken, kToken.substr(0, kToken.size() - 1)));
    CHECK_FALSE(ConstantTimeEquals(kToken, std::string(kToken) + "x"));
    CHECK_FALSE(ConstantTimeEquals(kToken, ""));
    CHECK_FALSE(ConstantTimeEquals("", "a"));
    CHECK_FALSE(ConstantTimeEquals("abc", std::string("abc\0", 4)));
    // Case matters.
    CHECK_FALSE(ConstantTimeEquals("abcDEF", "abcdef"));
}

TEST_CASE("usable tokens", "[local_mcp][token]") {
    CHECK(IsValidToken(kToken));
    CHECK(IsValidToken("a"));
    CHECK(IsValidToken("AZaz09-._~"));
    CHECK(IsValidToken(std::string(local_mcp::kMaxTokenLength, 'k')));
    CHECK_FALSE(IsValidToken(""));
    CHECK_FALSE(IsValidToken(std::string(local_mcp::kMaxTokenLength + 1, 'k')));
    for (const char* bad : {"has space", "a+b", "a%41", "a/b", "a=b", "a&b", "a?b", "a#b",
                            "a\"b", "tab\t", "new\nline", "vi\xE1\xBB\x87t"}) {
        CAPTURE(bad);
        CHECK_FALSE(IsValidToken(bad));
    }
    CHECK_FALSE(IsValidToken(std::string("a\0b", 3)));
}

TEST_CASE("handshake token check: missing", "[local_mcp][token]") {
    for (const char* target : {"/ws", "/ws?", "/ws?foo=bar", "/ws?token=", "/ws?token",
                               "/ws?Token=x", "/ws#token=x"}) {
        CAPTURE(target);
        CHECK(CheckToken(target, kToken) == TokenCheck::kMissing);
    }
}

TEST_CASE("handshake token check: wrong values are rejected", "[local_mcp][token]") {
    const std::string token(kToken);
    std::string upper = token;
    for (char& c : upper) {
        if (c >= 'a' && c <= 'z') {
            c = static_cast<char>(c - 'a' + 'A');
        }
    }
    for (const std::string& provided :
         {std::string("wrong"), token.substr(0, token.size() - 1), token + "x", upper,
          token.substr(1), std::string(local_mcp::kMaxTokenLength, 'k'),
          std::string(300, 'k')}) {
        CAPTURE(provided);
        CHECK(CheckToken(Target("token=" + provided), kToken) == TokenCheck::kRejected);
    }
    // Repeated parameter, even when one of them (or both) is right.
    CHECK(CheckToken(Target("token=" + token + "&token=wrong"), kToken) == TokenCheck::kRejected);
    CHECK(CheckToken(Target("token=wrong&token=" + token), kToken) == TokenCheck::kRejected);
    CHECK(CheckToken(Target("token=" + token + "&token=" + token), kToken) ==
          TokenCheck::kRejected);
    // Broken escape.
    CHECK(CheckToken(Target("token=" + token + "%"), kToken) == TokenCheck::kRejected);
    // A space where the client meant '+' (tokens never contain either).
    CHECK(CheckToken(Target("token=a+b"), "a+b") == TokenCheck::kRejected);
}

TEST_CASE("handshake token check: correct token", "[local_mcp][token]") {
    const std::string token(kToken);
    CHECK(CheckToken(Target("token=" + token), kToken) == TokenCheck::kAccepted);
    CHECK(CheckToken(Target("a=1&token=" + token + "&b=2"), kToken) == TokenCheck::kAccepted);
    CHECK(CheckToken(Target("token=" + token + "#x"), kToken) == TokenCheck::kAccepted);
    CHECK(CheckToken("http://helmet:8080/ws?token=" + token, kToken) == TokenCheck::kAccepted);
    // URL-encoded form of the same token (every byte escaped: 3 x 43 characters).
    CHECK(CheckToken(Target("token=" + PercentEncodeAll(token)), kToken) ==
          TokenCheck::kAccepted);
    // Longest token, sent raw and fully escaped.
    const std::string longest(local_mcp::kMaxTokenLength, 'Z');
    CHECK(CheckToken(Target("token=" + longest), longest) == TokenCheck::kAccepted);
    CHECK(CheckToken(Target("token=" + PercentEncodeAll(longest)), longest) ==
          TokenCheck::kAccepted);
}

TEST_CASE("handshake token check: an unusable configured token accepts nothing",
          "[local_mcp][token]") {
    CHECK(CheckToken("/ws?token=x", "") == TokenCheck::kRejected);
    CHECK(CheckToken("/ws?token=a%20b", "a b") == TokenCheck::kRejected);
    const std::string too_long(local_mcp::kMaxTokenLength + 1, 'k');
    CHECK(CheckToken(Target("token=" + too_long), too_long) == TokenCheck::kRejected);
}
