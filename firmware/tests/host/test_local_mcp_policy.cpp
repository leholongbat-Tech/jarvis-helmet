// Host tests for local_mcp_policy.h: what the unauthenticated local MCP WebSocket may call.
#include <catch2/catch_test_macros.hpp>

#include <cJSON.h>

#include <memory>
#include <string>

#include "helmet_common.h"
#include "local_mcp_policy.h"

namespace {

// RefusedToolCall() on a parsed request; returns "" when the request may go to McpServer.
std::string Refused(const char* json) {
    std::unique_ptr<cJSON, decltype(&cJSON_Delete)> root(cJSON_Parse(json), &cJSON_Delete);
    REQUIRE(root != nullptr);
    const char* name = local_mcp::RefusedToolCall(root.get());
    return name != nullptr ? std::string(name) : std::string();
}

}  // namespace

TEST_CASE("local MCP refuses tools/call of system tools, passes everything else",
          "[local_mcp]") {
    CHECK(Refused(R"({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{)"
                  R"("name":"self.upgrade_firmware","arguments":{"url":"http://x/evil.bin"}}})") ==
          "self.upgrade_firmware");
    CHECK(Refused(R"({"jsonrpc":"2.0","id":2,"method":"tools/call",)"
                  R"("params":{"name":"self.reboot","arguments":{}}})") == "self.reboot");
    CHECK(Refused(R"({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{)"
                  R"("name":"self.assets.set_download_url","arguments":{"url":"x"}}})") ==
          "self.assets.set_download_url");
    // Calibration and the helmet tools go through.
    CHECK(Refused(R"({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{)"
                  R"("name":"self.servo.calibrate","arguments":{"servo":0,"angle":95}}})")
              .empty());
    CHECK(Refused(R"({"jsonrpc":"2.0","id":5,"method":"tools/call",)"
                  R"("params":{"name":"self.mask.get_state"}})")
              .empty());
    // Other methods never call a tool.
    CHECK(Refused(R"({"jsonrpc":"2.0","id":6,"method":"tools/list",)"
                  R"("params":{"withUserTools":true}})")
              .empty());
    CHECK(Refused(R"({"jsonrpc":"2.0","id":7,"method":"initialize","params":{}})").empty());
    CHECK(Refused(R"({"jsonrpc":"2.0","id":8,"method":"tools/call "})").empty());
    // Malformed calls are rejected by McpServer without calling anything.
    CHECK(Refused(R"({"jsonrpc":"2.0","id":9,"method":"tools/call"})").empty());
    CHECK(Refused(R"({"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":5}})")
              .empty());
}

TEST_CASE("local MCP check reads the same fields as McpServer", "[local_mcp]") {
    // McpServer uses cJSON_GetObjectItem, which ignores key case: so does the check.
    CHECK(Refused(R"({"jsonrpc":"2.0","id":1,"Method":"tools/call",)"
                  R"("PARAMS":{"Name":"self.reboot"}})") == "self.reboot");
    // With duplicate keys both take the first one.
    CHECK(Refused(R"({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{)"
                  R"("name":"self.reboot","name":"self.mask.open"}})") == "self.reboot");
    CHECK(Refused(R"({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{)"
                  R"("name":"self.mask.open","name":"self.reboot"}})")
              .empty());
    CHECK(Refused(R"({"jsonrpc":"2.0","id":4,"method":"tools/list","method":"tools/call",)"
                  R"("params":{"name":"self.reboot"}})")
              .empty());
}

TEST_CASE("local MCP allows the helmet hardware and status tools", "[local_mcp]") {
    for (const char* name :
         {"self.mask.open", "self.mask.close", "self.mask.set_position", "self.mask.get_state",
          "self.servo.calibrate", "self.servo.save_calibration", "self.eyes.set",
          "self.eyes.effect", "self.output.set", "self.sequence.run", "self.sequence.run_saved",
          "self.sequence.save", "self.sequence.stop", "self.display.show_info",
          "self.system.get_status", "self.get_device_status"}) {
        CAPTURE(name);
        CHECK(local_mcp::IsToolAllowed(name));
    }
}

TEST_CASE("local MCP refuses upstream system tools", "[local_mcp]") {
    // Firmware flashing from any URL, reboot, asset URL, URL fetch/upload, system info.
    for (const char* name :
         {"self.upgrade_firmware", "self.reboot", "self.assets.set_download_url",
          "self.screen.snapshot", "self.screen.preview_image", "self.screen.get_info",
          "self.get_system_info", "self.audio_speaker.set_volume", "self.screen.set_theme"}) {
        CAPTURE(name);
        CHECK_FALSE(local_mcp::IsToolAllowed(name));
    }
    // Exact, case-sensitive matching (McpServer looks tools up the same way).
    CHECK_FALSE(local_mcp::IsToolAllowed(""));
    CHECK_FALSE(local_mcp::IsToolAllowed("self.mask."));
    CHECK_FALSE(local_mcp::IsToolAllowed("self.mask"));
    CHECK_FALSE(local_mcp::IsToolAllowed("SELF.MASK.OPEN"));
    CHECK_FALSE(local_mcp::IsToolAllowed("self.masked.open"));
    CHECK_FALSE(local_mcp::IsToolAllowed("self.system.get_status_x"));
    CHECK_FALSE(local_mcp::IsToolAllowed("x.self.mask.open"));
}

TEST_CASE("local MCP nesting limit fits real requests and stops stack overflows",
          "[local_mcp][json]") {
    const std::string calibrate =
        R"({"jsonrpc":"2.0","id":7,"method":"tools/call",)"
        R"("params":{"name":"self.servo.calibrate","arguments":{"servo":0,"angle":95}}})";
    CHECK(helmet::JsonNestingWithin(calibrate, local_mcp::kMaxNestingDepth));
    // A few hundred brackets (cJSON would recurse ~64 bytes each on the 6 KB httpd stack).
    CHECK_FALSE(helmet::JsonNestingWithin(std::string(200, '['), local_mcp::kMaxNestingDepth));
    CHECK_FALSE(helmet::JsonNestingWithin(std::string(300, '{'), local_mcp::kMaxNestingDepth));
    // The limit leaves at most 16 levels: ~1 KB of cJSON recursion.
    CHECK(local_mcp::kMaxNestingDepth * 64 <= 1024);
}
