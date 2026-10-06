// What the local MCP WebSocket (contract D7) may do, and who may open it (D27).
//
// Pure C++ plus cJSON (no ESP-IDF includes) so firmware/tests/host can check it. The
// endpoint exists for servo calibration and manual FW4 tests, so tools/call is limited to
// the helmet's own hardware tools plus read-only status. Upstream user-only tools such as
// self.reboot, self.upgrade_firmware (flashes a firmware image from any URL),
// self.assets.set_download_url and self.screen.snapshot / self.screen.preview_image (fetch
// or upload any URL) are refused: anyone on the same Wi-Fi network can reach this port.
// When an access token is provisioned (NVS jsecrets, namespace secrets, key mcp_token) the
// WebSocket handshake must also carry ?token=<value>.
#pragma once

#include <cJSON.h>

#include <cstddef>
#include <cstring>
#include <string>
#include <string_view>

namespace local_mcp {

// A text frame nests at most about 4 levels (envelope > payload > params > arguments);
// sequence steps travel as an escaped string. Deeper input is refused before cJSON, which
// recurses on the 6 KB httpd task stack.
constexpr int kMaxNestingDepth = 16;

inline bool IsToolAllowed(std::string_view name) {
    constexpr std::string_view kPrefixes[] = {"self.mask.", "self.servo.", "self.eyes.",
                                              "self.output.", "self.sequence."};
    constexpr std::string_view kNames[] = {"self.display.show_info", "self.system.get_status",
                                           "self.get_device_status"};
    for (std::string_view prefix : kPrefixes) {
        if (name.size() > prefix.size() && name.starts_with(prefix)) {
            return true;
        }
    }
    for (std::string_view allowed : kNames) {
        if (name == allowed) {
            return true;
        }
    }
    return false;
}

// Name of the tool a JSON-RPC request asks McpServer to call when this endpoint must refuse
// it, otherwise nullptr (not a tools/call, or one McpServer rejects without calling
// anything). Uses the same lookups as McpServer::ParseMessage (cJSON_GetObjectItem, which
// ignores key case, and an exact method name), so both read the same fields of the same
// parsed request.
inline const char* RefusedToolCall(const cJSON* request) {
    const cJSON* method = cJSON_GetObjectItem(request, "method");
    if (!cJSON_IsString(method) || method->valuestring == nullptr ||
        std::strcmp(method->valuestring, "tools/call") != 0) {
        return nullptr;
    }
    const cJSON* params = cJSON_GetObjectItem(request, "params");
    const cJSON* name = cJSON_IsObject(params) ? cJSON_GetObjectItem(params, "name") : nullptr;
    if (!cJSON_IsString(name) || name->valuestring == nullptr) {
        return nullptr;
    }
    return IsToolAllowed(name->valuestring) ? nullptr : name->valuestring;
}

// ---- Access token (contract D27) ------------------------------------------------------

// Longest token the firmware reads; tools/provision_secrets.py writes 16..64 characters.
constexpr std::size_t kMaxTokenLength = 64;

// A usable token has 1..64 characters from the URL "unreserved" set (A-Z a-z 0-9 - . _ ~),
// so a client never has to percent-encode it.
inline bool IsValidToken(std::string_view token) {
    if (token.empty() || token.size() > kMaxTokenLength) {
        return false;
    }
    for (char c : token) {
        const bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                        (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '~';
        if (!ok) {
            return false;
        }
    }
    return true;
}

// Equality check whose running time depends only on expected.size() (the stored token),
// never on the provided value or on the position of the first differing byte.
inline bool ConstantTimeEquals(std::string_view expected, std::string_view provided) {
    volatile unsigned char diff = expected.size() == provided.size() ? 0 : 1;
    for (std::size_t i = 0; i < expected.size(); ++i) {
        const unsigned char other =
            i < provided.size() ? static_cast<unsigned char>(provided[i]) : 0;
        diff = static_cast<unsigned char>(diff | (static_cast<unsigned char>(expected[i]) ^ other));
    }
    return diff == 0;
}

namespace detail {

inline int HexDigit(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

// Form decoding of one query component: %XX escapes, '+' is a space. False on a broken
// escape.
inline bool DecodeQueryComponent(std::string_view in, std::string& out) {
    out.clear();
    out.reserve(in.size());
    for (std::size_t i = 0; i < in.size(); ++i) {
        const char c = in[i];
        if (c == '+') {
            out.push_back(' ');
        } else if (c != '%') {
            out.push_back(c);
        } else {
            if (in.size() - i < 3) {
                return false;
            }
            const int high = HexDigit(in[i + 1]);
            const int low = HexDigit(in[i + 2]);
            if (high < 0 || low < 0) {
                return false;
            }
            out.push_back(static_cast<char>(high * 16 + low));
            i += 2;
        }
    }
    return true;
}

}  // namespace detail

enum class QueryValue { kMissing, kFound, kMalformed };

// Looks `key` up in the query of an HTTP request target such as "/ws?a=1&token=x" (also
// the absolute form "http://host/ws?..."; a #fragment is ignored). Keys compare exactly:
// case-sensitive and not decoded. The value is percent-decoded; a key without '=' has an
// empty value. kMalformed when the key appears more than once (no guessing which one
// counts) or its value has a broken %XX escape. Other parameters are not inspected.
inline QueryValue FindQueryValue(std::string_view target, std::string_view key,
                                 std::string& value) {
    value.clear();
    target = target.substr(0, target.find('#'));
    const std::size_t question = target.find('?');
    if (question == std::string_view::npos) {
        return QueryValue::kMissing;
    }
    std::string_view rest = target.substr(question + 1);
    QueryValue result = QueryValue::kMissing;
    while (!rest.empty()) {
        const std::size_t amp = rest.find('&');
        const std::string_view pair = rest.substr(0, amp);
        rest = amp == std::string_view::npos ? std::string_view() : rest.substr(amp + 1);
        const std::size_t eq = pair.find('=');
        if (pair.substr(0, eq) != key) {
            continue;
        }
        const std::string_view raw =
            eq == std::string_view::npos ? std::string_view() : pair.substr(eq + 1);
        if (result != QueryValue::kMissing || !detail::DecodeQueryComponent(raw, value)) {
            value.clear();
            return QueryValue::kMalformed;
        }
        result = QueryValue::kFound;
    }
    return result;
}

enum class TokenCheck {
    kAccepted,
    kMissing,   // no token parameter, or an empty one
    kRejected,  // wrong value, repeated parameter or broken escape
};

// Decision for the WebSocket handshake request target (e.g. "/ws?token=...") when the
// token `expected` is configured. An unusable `expected` (see IsValidToken) accepts
// nothing.
inline TokenCheck CheckToken(std::string_view target, std::string_view expected) {
    std::string provided;
    switch (FindQueryValue(target, "token", provided)) {
        case QueryValue::kMissing:
            return TokenCheck::kMissing;
        case QueryValue::kMalformed:
            return TokenCheck::kRejected;
        case QueryValue::kFound:
            break;
    }
    if (provided.empty()) {
        return TokenCheck::kMissing;
    }
    if (!IsValidToken(expected)) {
        return TokenCheck::kRejected;
    }
    return ConstantTimeEquals(expected, provided) ? TokenCheck::kAccepted
                                                  : TokenCheck::kRejected;
}

}  // namespace local_mcp
