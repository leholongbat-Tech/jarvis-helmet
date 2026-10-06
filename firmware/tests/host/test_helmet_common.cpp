// Host tests for main/boards/jarvis-helmet/helmet_common.h (shared limits and helpers).
#include <catch2/catch_test_macros.hpp>

#include <string>

#include "helmet_common.h"

TEST_CASE("ClampInt clamps into [lo, hi]", "[common]") {
    CHECK(helmet::ClampInt(5, 0, 10) == 5);
    CHECK(helmet::ClampInt(-5, 0, 10) == 0);
    CHECK(helmet::ClampInt(50, 0, 10) == 10);
}

TEST_CASE("MaskFullTravelMs scales with speed and clamps it", "[common][mask]") {
    CHECK(helmet::MaskFullTravelMs(100) == 800);
    CHECK(helmet::MaskFullTravelMs(80) == 1000);
    CHECK(helmet::MaskFullTravelMs(70) == 1142);  // integer division, as on the server
    CHECK(helmet::MaskFullTravelMs(10) == 8000);
    CHECK(helmet::MaskFullTravelMs(5) == 8000);    // clamped to speed 10
    CHECK(helmet::MaskFullTravelMs(1000) == 800);  // clamped to speed 100
    CHECK(helmet::MaskFullTravelMs(-1) == 8000);
}

TEST_CASE("MaskMoveMs scales with distance with a minimum", "[common][mask]") {
    CHECK(helmet::MaskMoveMs(0, 100, 70) == 1142);
    CHECK(helmet::MaskMoveMs(100, 0, 80) == 1000);
    CHECK(helmet::MaskMoveMs(0, 50, 100) == 400);
    CHECK(helmet::MaskMoveMs(20, 70, 50) == 800);
    CHECK(helmet::MaskMoveMs(50, 51, 100) == helmet::kMaskMinMoveMs);  // 8 ms -> 150 ms
    CHECK(helmet::MaskMoveMs(30, 30, 70) == 0);
    // Positions are clamped to 0..100 before the distance is taken.
    CHECK(helmet::MaskMoveMs(-20, 150, 100) == 800);
    CHECK(helmet::MaskMoveMs(100, 120, 100) == 0);
}

TEST_CASE("sequence names follow [a-z0-9_]{1,11}", "[common][names]") {
    CHECK(helmet::IsValidSequenceName("open_mask"));
    CHECK(helmet::IsValidSequenceName("close_mask"));
    CHECK(helmet::IsValidSequenceName("a"));
    CHECK(helmet::IsValidSequenceName("mode_2"));
    CHECK(helmet::IsValidSequenceName("abcdefghijk"));  // 11 chars: "seq_" + 11 = 15
    CHECK_FALSE(helmet::IsValidSequenceName("abcdefghijkl"));
    CHECK_FALSE(helmet::IsValidSequenceName(""));
    CHECK_FALSE(helmet::IsValidSequenceName("Open"));
    CHECK_FALSE(helmet::IsValidSequenceName("open-mask"));
    CHECK_FALSE(helmet::IsValidSequenceName("open mask"));
    CHECK_FALSE(helmet::IsValidSequenceName("mở"));
}

TEST_CASE("sound names follow [a-z0-9_]{1,32}", "[common][names]") {
    CHECK(helmet::IsValidSoundName("power_up"));
    CHECK(helmet::IsValidSoundName(std::string(32, 'z')));
    CHECK_FALSE(helmet::IsValidSoundName(std::string(33, 'z')));
    CHECK_FALSE(helmet::IsValidSoundName(""));
    CHECK_FALSE(helmet::IsValidSoundName("Beep"));
    CHECK_FALSE(helmet::IsValidSoundName("beep.ogg"));
    CHECK_FALSE(helmet::IsValidSoundName("beep\n"));  // a trailing newline is not [a-z0-9_]
}

TEST_CASE("eye effect names", "[common][names]") {
    for (const char* name : {"breathe", "blink", "flicker", "power_up", "fade_out"}) {
        CHECK(helmet::IsEyeEffect(name));
    }
    CHECK_FALSE(helmet::IsEyeEffect(""));
    CHECK_FALSE(helmet::IsEyeEffect("Blink"));
    CHECK_FALSE(helmet::IsEyeEffect("rainbow"));
}

TEST_CASE("Utf8Length counts code points", "[common][utf8]") {
    CHECK(helmet::Utf8Length("") == 0);
    CHECK(helmet::Utf8Length("abc") == 3);
    CHECK(helmet::Utf8Length("Hệ thống") == 8);
    CHECK(helmet::Utf8Length("Ệ") == 1);
    CHECK(helmet::Utf8Length("Đã mở mặt nạ, thưa ngài.") == 24);
    CHECK(helmet::Utf8Length("😀") == 1);
    CHECK(std::string("😀").size() == 4);
    // Stray continuation bytes are not counted.
    CHECK(helmet::Utf8Length(std::string("a\x80\x80") + "b") == 2);
}

TEST_CASE("SanitizeMessage makes text safe to paste into a JSON string", "[common][errors]") {
    CHECK(helmet::SanitizeMessage("plain text") == "plain text");
    CHECK(helmet::SanitizeMessage("say \"hi\"") == "say 'hi'");
    CHECK(helmet::SanitizeMessage("a\\b") == "a/b");
    CHECK(helmet::SanitizeMessage("line1\nline2\r\t") == "line1 line2  ");
    CHECK(helmet::SanitizeMessage(std::string("x\x7f") + "y") == "x y");
    CHECK(helmet::SanitizeMessage(std::string("nul\0end", 7)) == "nul end");
    // UTF-8 passes through unchanged.
    CHECK(helmet::SanitizeMessage("Đã mở mặt nạ") == "Đã mở mặt nạ");
}

TEST_CASE("limits match docs/interfaces.md", "[common][limits]") {
    CHECK(helmet::kMaxSteps == 64);
    CHECK(helmet::kMaxSequenceMs == 60000);
    CHECK(helmet::kSequenceNameMaxLen == 11);
    CHECK(helmet::kInfoTitleMaxChars == 24);
    CHECK(helmet::kInfoTextMaxChars == 200);
    CHECK(helmet::kDefaultSpeed == 70);
    CHECK(helmet::kDefaultEffectDurationMs == 1000);
    CHECK(helmet::kDefaultInfoSeconds == 5);
}

TEST_CASE("JsonNestingWithin bounds bracket depth outside strings", "[common][json]") {
    CHECK(helmet::JsonNestingWithin("", 1));
    CHECK(helmet::JsonNestingWithin("42", 0));
    CHECK(helmet::JsonNestingWithin("[]", 1));
    CHECK_FALSE(helmet::JsonNestingWithin("[]", 0));
    CHECK(helmet::JsonNestingWithin(std::string(16, '[') + std::string(16, ']'), 16));
    CHECK_FALSE(helmet::JsonNestingWithin(std::string(17, '[') + std::string(17, ']'), 16));
    // Depth is counted while scanning, so unbalanced input is refused as early as possible.
    CHECK_FALSE(helmet::JsonNestingWithin(std::string(300, '['), 16));
    std::string objects;
    for (int i = 0; i < 300; ++i) {
        objects += "{\"a\":";
    }
    CHECK_FALSE(helmet::JsonNestingWithin(objects, 16));
    // Siblings do not add up.
    CHECK(helmet::JsonNestingWithin("[[1],[2],[3],{\"a\":[4]}]", 3));
    // Brackets inside strings (also after escaped quotes and backslashes) do not count.
    CHECK(helmet::JsonNestingWithin("[\"[[[[{{{{\"]", 1));
    CHECK(helmet::JsonNestingWithin("[\"a\\\"[[[[\"]", 1));
    CHECK_FALSE(helmet::JsonNestingWithin("[\"a\\\\\"[[]]]", 1));  // "a\\" closes the string
    // Stray closing brackets cannot make room for deeper nesting.
    CHECK_FALSE(helmet::JsonNestingWithin("]]]][[[", 2));
    // The real local MCP request shape: envelope > payload > params > arguments.
    const std::string request =
        R"({"type":"mcp","payload":{"jsonrpc":"2.0","id":3,"method":"tools/call",)"
        R"("params":{"name":"self.sequence.run","arguments":{"steps":"[{\"delay\":100}]"}}}})";
    CHECK(helmet::JsonNestingWithin(request, 4));
    CHECK_FALSE(helmet::JsonNestingWithin(request, 3));
    CHECK(helmet::JsonNestingWithin(request.data(), request.size(), 4));
}
