// Host tests for main/boards/jarvis-helmet/sequence_validator.cc (docs/interfaces.md §3,
// contract D23: errors are "invalid step #<n>: <reason>" with n counted from 1).
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <cJSON.h>

#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "helmet_common.h"
#include "sequence_validator.h"

using Catch::Matchers::ContainsSubstring;
using Catch::Matchers::StartsWith;

namespace {

std::string Repeat(const std::string& s, int n) {
    std::string out;
    for (int i = 0; i < n; ++i) {
        out += s;
    }
    return out;
}

// n comma-separated copies of one step inside an array.
std::string Steps(const std::string& step, int n) {
    std::string out = "[";
    for (int i = 0; i < n; ++i) {
        out += (i > 0 ? "," : "") + step;
    }
    return out + "]";
}

seq::Step ValidStep(const std::string& step_json) {
    seq::ValidationResult r = seq::Validate("[" + step_json + "]");
    INFO("step: " << step_json << " -> " << r.error);
    REQUIRE(r.ok);
    REQUIRE(r.sequence.steps.size() == 1);
    return r.sequence.steps[0];
}

void ExpectValid(const std::string& json) {
    seq::ValidationResult r = seq::Validate(json);
    INFO("json: " << json << " -> " << r.error);
    CHECK(r.ok);
    CHECK(r.error.empty());
}

void ExpectStepError(const std::string& json, int number, const std::string& part) {
    seq::ValidationResult r = seq::Validate(json);
    INFO("json: " << json << " -> " << r.error);
    REQUIRE_FALSE(r.ok);
    CHECK_THAT(r.error, StartsWith("invalid step #" + std::to_string(number) + ": "));
    CHECK_THAT(r.error, ContainsSubstring(part));
}

// Single-step shorthand: the step is #1.
void ExpectStep1Error(const std::string& step_json, const std::string& part) {
    ExpectStepError("[" + step_json + "]", 1, part);
}

void ExpectTopError(const std::string& json, const std::string& part) {
    seq::ValidationResult r = seq::Validate(json);
    INFO("json: " << json << " -> " << r.error);
    REQUIRE_FALSE(r.ok);
    CHECK_THAT(r.error, !StartsWith("invalid step #"));
    CHECK_THAT(r.error, ContainsSubstring(part));
}

bool JsonSafe(const std::string& s) {
    for (unsigned char c : s) {
        if (c == '"' || c == '\\' || c < 0x20 || c == 0x7F) {
            return false;
        }
    }
    return true;
}

// docs/interfaces.md §5 (tasks.yaml) and §3 (format example).
const std::string kOpenMask =
    R"([{"eyes":{"effect":"fade_out","duration_ms":300}},{"mask":{"action":"open","speed":80}}])";
const std::string kCloseMask =
    R"([{"mask":{"action":"close"}},{"delay":300},{"eyes":{"effect":"power_up","duration_ms":1200}}])";
const std::string kCombatExample = R"([
  {"eyes":    {"effect": "fade_out", "duration_ms": 300}},
  {"mask":    {"action": "open", "speed": 80}},
  {"delay":   300},
  {"eyes":    {"on": true, "brightness": 100}},
  {"output":  {"on": true}},
  {"servo":   {"id": 0, "angle": 45, "duration_ms": 500}},
  {"display": {"title": "COMBAT MODE", "text": "Hệ thống vũ khí sẵn sàng", "seconds": 3}},
  {"sound":   {"name": "power_up"}}
])";

}  // namespace

TEST_CASE("empty array is a valid no-op", "[validator]") {
    seq::ValidationResult r = seq::Validate("[]");
    CHECK(r.ok);
    CHECK(r.sequence.steps.empty());
    CHECK(r.sequence.estimated_ms == 0);
    ExpectValid(" \n [ ] \t ");
}

TEST_CASE("interfaces.md open_mask runs fade_out then opens at speed 80", "[validator][samples]") {
    seq::ValidationResult r = seq::Validate(kOpenMask);
    REQUIRE(r.ok);
    REQUIRE(r.sequence.steps.size() == 2);
    const seq::Step& eyes = r.sequence.steps[0];
    CHECK(eyes.type == seq::StepType::kEyes);
    CHECK(eyes.eyes_effect);
    CHECK(eyes.effect == "fade_out");
    CHECK(eyes.duration_ms == 300);
    const seq::Step& mask = r.sequence.steps[1];
    CHECK(mask.type == seq::StepType::kMask);
    CHECK(mask.mask_by_action);
    CHECK(mask.mask_position == 100);
    CHECK(mask.speed == 80);
    CHECK(r.sequence.estimated_ms == 300 + 1000);
}

TEST_CASE("interfaces.md close_mask uses the default speed", "[validator][samples]") {
    seq::ValidationResult r = seq::Validate(kCloseMask);
    REQUIRE(r.ok);
    REQUIRE(r.sequence.steps.size() == 3);
    CHECK(r.sequence.steps[0].type == seq::StepType::kMask);
    CHECK(r.sequence.steps[0].mask_position == 0);
    CHECK(r.sequence.steps[0].speed == -1);
    CHECK(r.sequence.steps[1].type == seq::StepType::kDelay);
    CHECK(r.sequence.steps[1].delay_ms == 300);
    CHECK(r.sequence.steps[2].effect == "power_up");
    CHECK(r.sequence.steps[2].duration_ms == 1200);
    // Mask estimate is a full travel at the default speed 70: 800 * 100 / 70 = 1142.
    CHECK(r.sequence.estimated_ms == 1142 + 300 + 1200);
}

TEST_CASE("interfaces.md format example parses every step type", "[validator][samples]") {
    seq::ValidationResult r = seq::Validate(kCombatExample);
    INFO(r.error);
    REQUIRE(r.ok);
    const auto& s = r.sequence.steps;
    REQUIRE(s.size() == 8);
    CHECK(s[0].type == seq::StepType::kEyes);
    CHECK(s[1].type == seq::StepType::kMask);
    CHECK(s[2].type == seq::StepType::kDelay);
    CHECK(s[2].delay_ms == 300);
    CHECK(s[3].type == seq::StepType::kEyes);
    CHECK_FALSE(s[3].eyes_effect);
    CHECK(s[3].eyes_on);
    CHECK(s[3].brightness == 100);
    CHECK(s[4].type == seq::StepType::kOutput);
    CHECK(s[4].output_on);
    CHECK(s[5].type == seq::StepType::kServo);
    CHECK(s[5].servo_id == 0);
    CHECK(s[5].angle == 45);
    CHECK(s[5].duration_ms == 500);
    CHECK(s[6].type == seq::StepType::kDisplay);
    CHECK(s[6].title == "COMBAT MODE");
    CHECK(s[6].text == "Hệ thống vũ khí sẵn sàng");
    CHECK(s[6].seconds == 3);
    CHECK(s[7].type == seq::StepType::kSound);
    CHECK(s[7].sound == "power_up");
    CHECK(r.sequence.estimated_ms == 300 + 1000 + 300 + 500);
}

TEST_CASE("optional fields get their defaults", "[validator]") {
    CHECK(ValidStep(R"({"mask":{"action":"open"}})").speed == -1);
    CHECK(ValidStep(R"({"mask":{"position":40}})").mask_position == 40);
    CHECK_FALSE(ValidStep(R"({"mask":{"position":40}})").mask_by_action);
    CHECK(ValidStep(R"({"eyes":{"on":true}})").brightness == -1);
    CHECK(ValidStep(R"({"eyes":{"effect":"blink"}})").duration_ms == 1000);
    CHECK(ValidStep(R"({"servo":{"id":1,"angle":90}})").duration_ms == 0);
    CHECK(ValidStep(R"({"display":{"title":"T","text":"x"}})").seconds == 5);
    CHECK_FALSE(ValidStep(R"({"eyes":{"on":false,"brightness":30}})").eyes_on);
    CHECK_FALSE(ValidStep(R"({"output":{"on":false}})").output_on);
}

TEST_CASE("top level must be a JSON array", "[validator][top]") {
    for (const char* json :
         {"{}", R"("steps")", "5", "null", "true", R"({"mask":{"action":"open"}})"}) {
        seq::ValidationResult r = seq::Validate(json);
        INFO(json);
        REQUIRE_FALSE(r.ok);
        CHECK(r.error == "steps must be a JSON array");
    }
}

TEST_CASE("text that is not JSON is rejected", "[validator][top]") {
    for (const char* json :
         {"", " ", "[", "[1,]", R"([{"delay":1})", "[] trailing", "[]]", "[{delay:1}]", "['x']"}) {
        ExpectTopError(json, "steps must be a JSON array");
    }
}

TEST_CASE("absurd nesting is rejected before parsing", "[validator][top]") {
    ExpectTopError(Repeat("[", 20000) + Repeat("]", 20000), "nesting too deep");
    ExpectTopError(Repeat("[", 17) + Repeat("]", 17), "nesting too deep");
    // Brackets inside strings do not count.
    ExpectValid(R"([{"display":{"title":"[[[[{{{{","text":")" + Repeat("[{", 50) + R"("}}])");
    // Moderately deep but wrong values still get a precise step error.
    ExpectStep1Error(R"({"mask":{"action":[[1]]}})", "action must be a string");
}

TEST_CASE("unknown step types are rejected", "[validator][keys]") {
    ExpectStep1Error(R"({"masks":{"action":"open"}})", "unknown step type masks");
    ExpectStep1Error(R"({"Mask":{"action":"open"}})", "unknown step type Mask");
    ExpectStep1Error(R"({"wait":300})", "unknown step type wait");
    ExpectStep1Error(R"({"":300})", "unknown step type ''");
}

TEST_CASE("a step object has exactly one key", "[validator][keys]") {
    ExpectStep1Error(R"({"delay":100,"mask":{"action":"open"}})", "exactly one key (got 2)");
    ExpectStep1Error(R"({"delay":100,"delay":200})", "exactly one key (got 2)");
    ExpectStep1Error(R"({})", "exactly one key (got 0)");
}

TEST_CASE("a step must be an object", "[validator][keys]") {
    for (const char* step : {"300", R"("mask")", R"([{"delay":1}])", "null", "true"}) {
        ExpectStep1Error(step, "step must be an object");
    }
}

TEST_CASE("unknown and duplicate fields are rejected", "[validator][fields]") {
    ExpectStep1Error(R"({"mask":{"action":"open","foo":1}})", "unknown field foo in mask");
    ExpectStep1Error(R"({"mask":{"Action":"open"}})", "unknown field Action in mask");
    ExpectStep1Error(R"({"servo":{"id":0,"angle":10,"speed":50}})", "unknown field speed in servo");
    ExpectStep1Error(R"({"eyes":{"on":true,"color":"red"}})", "unknown field color in eyes");
    ExpectStep1Error(R"({"output":{"on":true,"brightness":50}})",
                     "unknown field brightness in output");
    ExpectStep1Error(R"({"display":{"title":"A","text":"B","subtitle":"C"}})",
                     "unknown field subtitle in display");
    ExpectStep1Error(R"({"sound":{"name":"beep","volume":3}})", "unknown field volume in sound");
    ExpectStep1Error(R"({"mask":{"action":"open","speed":50,"speed":60}})",
                     "duplicate field speed in mask");
}

TEST_CASE("mask rules", "[validator][mask]") {
    ExpectStep1Error(R"({"mask":{"action":"open","position":50}})",
                     "mask needs exactly one of action or position");
    ExpectStep1Error(R"({"mask":{"speed":50}})", "mask needs exactly one of action or position");
    ExpectStep1Error(R"({"mask":{}})", "mask needs exactly one of action or position");
    ExpectStep1Error(R"({"mask":{"action":"lift"}})", "action must be open or close");
    ExpectStep1Error(R"({"mask":{"action":"OPEN"}})", "action must be open or close");
    ExpectStep1Error(R"({"mask":{"action":1}})", "action must be a string");
    ExpectStep1Error(R"({"mask":{"position":101}})", "position out of range 0..100");
    ExpectStep1Error(R"({"mask":{"position":-1}})", "position out of range 0..100");
    ExpectStep1Error(R"({"mask":{"position":50.5}})", "position must be an integer");
    ExpectStep1Error(R"({"mask":{"action":"open","speed":9}})", "speed out of range 10..100");
    ExpectStep1Error(R"({"mask":{"action":"open","speed":101}})", "speed out of range 10..100");
    ExpectStep1Error(R"({"mask":"open"})", "mask must be an object");
    CHECK(ValidStep(R"({"mask":{"position":0,"speed":10}})").speed == 10);
    CHECK(ValidStep(R"({"mask":{"position":100,"speed":100}})").mask_position == 100);
    CHECK(ValidStep(R"({"mask":{"action":"close"}})").mask_position == 0);
}

TEST_CASE("servo rules", "[validator][servo]") {
    ExpectStep1Error(R"({"servo":{"id":2,"angle":90}})", "id out of range 0..1");
    ExpectStep1Error(R"({"servo":{"id":-1,"angle":90}})", "id out of range 0..1");
    ExpectStep1Error(R"({"servo":{"id":0,"angle":181}})", "angle out of range 0..180");
    ExpectStep1Error(R"({"servo":{"id":0,"angle":-5}})", "angle out of range 0..180");
    ExpectStep1Error(R"({"servo":{"id":0,"angle":90,"duration_ms":5001}})",
                     "duration_ms out of range 0..5000");
    ExpectStep1Error(R"({"servo":{"id":0,"angle":90,"duration_ms":-1}})",
                     "duration_ms out of range 0..5000");
    ExpectStep1Error(R"({"servo":{"angle":90}})", "missing field id");
    ExpectStep1Error(R"({"servo":{"id":1}})", "missing field angle");
    ExpectStep1Error(R"({"servo":{"id":"0","angle":90}})", "id must be an integer");
    seq::Step step = ValidStep(R"({"servo":{"id":1,"angle":180,"duration_ms":5000}})");
    CHECK(step.servo_id == 1);
    CHECK(step.angle == 180);
    CHECK(seq::EstimateStepMs(step) == 5000);
    CHECK(ValidStep(R"({"servo":{"id":0,"angle":0,"duration_ms":0}})").angle == 0);
}

TEST_CASE("eyes rules", "[validator][eyes]") {
    ExpectStep1Error(R"({"eyes":{"on":true,"brightness":101}})", "brightness out of range 0..100");
    ExpectStep1Error(R"({"eyes":{"on":true,"brightness":-1}})", "brightness out of range 0..100");
    ExpectStep1Error(R"({"eyes":{"on":1}})", "on must be a boolean");
    ExpectStep1Error(R"({"eyes":{"on":"true"}})", "on must be a boolean");
    ExpectStep1Error(R"({"eyes":{"effect":"sparkle"}})", "unknown effect sparkle");
    ExpectStep1Error(R"({"eyes":{"effect":"Blink"}})", "unknown effect Blink");
    ExpectStep1Error(R"({"eyes":{"effect":7}})", "effect must be a string");
    ExpectStep1Error(R"({"eyes":{"effect":"blink","duration_ms":99}})",
                     "duration_ms out of range 100..10000");
    ExpectStep1Error(R"({"eyes":{"effect":"blink","duration_ms":10001}})",
                     "duration_ms out of range 100..10000");
    ExpectStep1Error(R"({"eyes":{"on":true,"effect":"blink"}})",
                     "eyes cannot mix on/brightness with effect/duration_ms");
    ExpectStep1Error(R"({"eyes":{"brightness":50,"duration_ms":500}})",
                     "eyes cannot mix on/brightness with effect/duration_ms");
    ExpectStep1Error(R"({"eyes":{"brightness":50}})", "missing field on");
    ExpectStep1Error(R"({"eyes":{"duration_ms":500}})", "missing field effect");
    ExpectStep1Error(R"({"eyes":{}})", "eyes needs on or effect");
    for (const char* effect : helmet::kEyeEffects) {
        seq::Step step =
            ValidStep(std::string(R"({"eyes":{"effect":")") + effect + R"(","duration_ms":2000}})");
        CHECK(step.effect == effect);
        // breathe repeats until the next eyes command, so it does not block the sequence.
        CHECK(seq::EstimateStepMs(step) == (std::string(effect) == "breathe" ? 0 : 2000));
    }
    CHECK(seq::EstimateStepMs(ValidStep(R"({"eyes":{"on":true,"brightness":0}})")) == 0);
}

TEST_CASE("output rules", "[validator][output]") {
    ExpectStep1Error(R"({"output":{}})", "missing field on");
    ExpectStep1Error(R"({"output":{"on":1}})", "on must be a boolean");
    ExpectStep1Error(R"({"output":{"on":null}})", "on must be a boolean");
    ExpectStep1Error(R"({"output":true})", "output must be an object");
    CHECK(ValidStep(R"({"output":{"on":true}})").output_on);
}

TEST_CASE("delay rules", "[validator][delay]") {
    ExpectStep1Error(R"({"delay":10001})", "delay out of range 0..10000");
    ExpectStep1Error(R"({"delay":-1})", "delay out of range 0..10000");
    ExpectStep1Error(R"({"delay":1e400})", "delay out of range 0..10000");
    ExpectStep1Error(R"({"delay":"300"})", "delay must be an integer");
    ExpectStep1Error(R"({"delay":300.5})", "delay must be an integer");
    ExpectStep1Error(R"({"delay":true})", "delay must be an integer");
    ExpectStep1Error(R"({"delay":null})", "delay must be an integer");
    ExpectStep1Error(R"({"delay":{"ms":300}})", "delay must be an integer");
    CHECK(ValidStep(R"({"delay":0})").delay_ms == 0);
    CHECK(ValidStep(R"({"delay":10000})").delay_ms == 10000);
    // JSON has one number type: whole-valued numbers are integers (as in JSON Schema).
    CHECK(ValidStep(R"({"delay":300.0})").delay_ms == 300);
    CHECK(ValidStep(R"({"delay":1e3})").delay_ms == 1000);
}

TEST_CASE("wrong JSON types and floats for integer and boolean fields", "[validator][types]") {
    ExpectStep1Error(R"({"mask":{"action":"open","speed":"80"}})", "speed must be an integer");
    ExpectStep1Error(R"({"mask":{"action":"open","speed":80.5}})", "speed must be an integer");
    ExpectStep1Error(R"({"mask":{"action":"open","speed":true}})", "speed must be an integer");
    ExpectStep1Error(R"({"servo":{"id":0,"angle":45.25}})", "angle must be an integer");
    ExpectStep1Error(R"({"eyes":{"on":true,"brightness":99.9}})", "brightness must be an integer");
    ExpectStep1Error(R"({"eyes":{"effect":"blink","duration_ms":"500"}})",
                     "duration_ms must be an integer");
    ExpectStep1Error(R"({"display":{"title":"A","text":"B","seconds":2.5}})",
                     "seconds must be an integer");
    ExpectStep1Error(R"({"output":{"on":"yes"}})", "on must be a boolean");
    ExpectStep1Error(R"({"display":{"title":5,"text":"B"}})", "title must be a string");
    ExpectStep1Error(R"({"display":{"title":"A","text":null}})", "text must be a string");
    ExpectStep1Error(R"({"sound":{"name":5}})", "name must be a string");
}

TEST_CASE("display rules", "[validator][display]") {
    ExpectStep1Error(R"({"display":{"text":"x"}})", "missing field title");
    ExpectStep1Error(R"({"display":{"title":"x"}})", "missing field text");
    // Text-only card: the title is required but may be empty (same as the server).
    CHECK(ValidStep(R"({"display":{"title":"","text":"x"}})").title.empty());
    ExpectStep1Error(R"({"display":{"title":"A","text":"B","seconds":0}})",
                     "seconds out of range 1..30");
    ExpectStep1Error(R"({"display":{"title":"A","text":"B","seconds":31}})",
                     "seconds out of range 1..30");
    ExpectStep1Error(R"({"display":"hello"})", "display must be an object");
    CHECK(ValidStep(R"({"display":{"title":"A","text":"","seconds":1}})").text.empty());
    CHECK(ValidStep(R"({"display":{"title":"A","text":"B","seconds":30}})").seconds == 30);
    CHECK(seq::EstimateStepMs(ValidStep(R"({"display":{"title":"A","text":"B"}})")) == 0);
}

TEST_CASE("display text limits count UTF-8 code points (Vietnamese)", "[validator][display]") {
    // 24 code points, 33 bytes: the interfaces.md example text used as a title.
    const std::string title24 = "Hệ thống vũ khí sẵn sàng";
    REQUIRE(helmet::Utf8Length(title24) == 24);
    REQUIRE(title24.size() > 24);
    CHECK(ValidStep(R"({"display":{"title":")" + title24 + R"(","text":"ok"}})").title == title24);
    ExpectStep1Error(R"({"display":{"title":")" + title24 + R"(!","text":"ok"}})",
                     "title too long (max 24 chars)");
    // 3-byte letters: 24 fit (72 bytes), 25 do not.
    ExpectValid(R"([{"display":{"title":")" + Repeat("Ữ", 24) + R"(","text":""}}])");
    ExpectStep1Error(R"({"display":{"title":")" + Repeat("Ữ", 25) + R"(","text":""}})",
                     "title too long (max 24 chars)");
    // Text: 200 code points of 3 bytes (600 bytes) fit, 201 do not.
    ExpectValid(R"([{"display":{"title":"T","text":")" + Repeat("ặ", 200) + R"("}}])");
    ExpectStep1Error(R"({"display":{"title":"T","text":")" + Repeat("ặ", 201) + R"("}})",
                     "text too long (max 200 chars)");
    // \u escapes decode to the same code points.
    ExpectValid(R"([{"display":{"title":")" + Repeat("\\u1ec7", 24) + R"(","text":""}}])");
    // Combining marks are separate code points (decomposed text is longer; send NFC).
    ExpectStep1Error(R"({"display":{"title":")" + Repeat("ệ", 9) + R"(","text":""}})",
                     "title too long (max 24 chars)");
    // 4-byte code points (emoji) count once each.
    ExpectValid(R"([{"display":{"title":")" + Repeat("😀", 24) + R"(","text":""}}])");
}

TEST_CASE("sound rules", "[validator][sound]") {
    CHECK(ValidStep(R"({"sound":{"name":"power_up"}})").sound == "power_up");
    CHECK(ValidStep(R"({"sound":{"name":")" + Repeat("a", 32) + R"("}})").sound.size() == 32);
    ExpectStep1Error(R"({"sound":{"name":")" + Repeat("a", 33) + R"("}})",
                     "sound name must match [a-z0-9_]{1,32}");
    ExpectStep1Error(R"({"sound":{"name":"Power-Up"}})", "sound name must match");
    ExpectStep1Error(R"({"sound":{"name":""}})", "sound name must match");
    ExpectStep1Error(R"({"sound":{}})", "missing field name");
    CHECK(seq::EstimateStepMs(ValidStep(R"({"sound":{"name":"x"}})")) == 0);
}

TEST_CASE("at most 64 steps", "[validator][limits]") {
    seq::ValidationResult ok = seq::Validate(Steps(R"({"output":{"on":true}})", 64));
    CHECK(ok.ok);
    CHECK(ok.sequence.steps.size() == 64);
    seq::ValidationResult too_many = seq::Validate(Steps(R"({"output":{"on":true}})", 65));
    REQUIRE_FALSE(too_many.ok);
    CHECK(too_many.error == "too many steps (max 64)");
    // The count is checked before the steps themselves.
    CHECK(seq::Validate(Steps(R"({"bogus":1})", 65)).error == "too many steps (max 64)");
}

TEST_CASE("estimated total is capped at 60 s", "[validator][limits]") {
    seq::ValidationResult exactly = seq::Validate(Steps(R"({"delay":10000})", 6));
    CHECK(exactly.ok);
    CHECK(exactly.sequence.estimated_ms == 60000);

    seq::ValidationResult too_long = seq::Validate(Steps(R"({"delay":10000})", 7));
    REQUIRE_FALSE(too_long.ok);
    CHECK(too_long.error == "sequence too long: 70000 ms (max 60000)");
    CHECK(too_long.sequence.estimated_ms == 70000);

    // Mask steps count a full travel at their speed: 7 x 8000 + 4001 = 60001.
    seq::ValidationResult masks = seq::Validate(
        "[" + Repeat(R"({"mask":{"action":"open","speed":10}},)", 7) + R"({"delay":4001}])");
    REQUIRE_FALSE(masks.ok);
    CHECK(masks.error == "sequence too long: 60001 ms (max 60000)");

    // Effects count their duration, breathe counts 0.
    CHECK(seq::Validate(Steps(R"({"eyes":{"effect":"breathe","duration_ms":10000}})", 64)).ok);
    CHECK_FALSE(seq::Validate(Steps(R"({"eyes":{"effect":"blink","duration_ms":10000}})", 7)).ok);
    // A step error is reported before the duration check.
    CHECK_THAT(seq::Validate(Steps(R"({"delay":10000})", 7).insert(1, R"({"delay":-1},)")).error,
               StartsWith("invalid step #1: "));
}

TEST_CASE("mask estimate is a full travel at the step speed", "[validator][estimate]") {
    CHECK(seq::EstimateStepMs(ValidStep(R"({"mask":{"action":"open","speed":10}})")) == 8000);
    CHECK(seq::EstimateStepMs(ValidStep(R"({"mask":{"action":"open","speed":80}})")) == 1000);
    CHECK(seq::EstimateStepMs(ValidStep(R"({"mask":{"action":"open","speed":100}})")) == 800);
    CHECK(seq::EstimateStepMs(ValidStep(R"({"mask":{"action":"close"}})")) == 1142);
    CHECK(seq::EstimateStepMs(ValidStep(R"({"mask":{"position":55,"speed":50}})")) == 1600);
    CHECK(seq::EstimateStepMs(ValidStep(R"({"delay":1234})")) == 1234);
    CHECK(seq::EstimateStepMs(ValidStep(R"({"output":{"on":true}})")) == 0);
}

TEST_CASE("errors are numbered from 1 and the first bad step wins", "[validator][numbering]") {
    seq::ValidationResult first = seq::Validate(R"([{"delay":-3},{"delay":1}])");
    CHECK(first.error == "invalid step #1: delay out of range 0..10000");
    seq::ValidationResult third = seq::Validate(R"([{"delay":1},{"delay":2},{"delay":-3}])");
    CHECK(third.error == "invalid step #3: delay out of range 0..10000");
    ExpectStepError(R"([{"delay":1},{"nope":1},{"delay":1},{"mask":{}}])", 2,
                    "unknown step type nope");
    std::string last_bad = Steps(R"({"delay":1})", 63);
    last_bad.insert(last_bad.size() - 1, R"(,{"mask":{"speed":5}})");
    ExpectStepError(last_bad, 64, "mask needs exactly one of action or position");
}

TEST_CASE("error text is JSON-safe and bounded", "[validator][errors]") {
    const std::vector<std::string> bad = {
        R"([{"ma\"sk\\\n\t\u0001":1}])",           // quote, backslash, controls in a key
        R"([{"mask":{"act\"ion":"open"}}])",       // quote in a field name
        R"([{"eyes":{"effect":"bl\"i\\nk\r"}}])",  // quote, backslash, CR in a value
        R"([{"Mẫu":1}])",                          // non-ASCII key
        "[{\"" + Repeat("x", 300) + "\":1}]",      // very long key
        "[{\"delay\":1}] \"",                      // trailing garbage
    };
    for (const std::string& json : bad) {
        seq::ValidationResult r = seq::Validate(json);
        INFO("json: " << json << " -> " << r.error);
        REQUIRE_FALSE(r.ok);
        CHECK(JsonSafe(r.error));
        CHECK(r.error.find('"') == std::string::npos);
        CHECK(r.error.find('\\') == std::string::npos);
        CHECK(r.error.find('\n') == std::string::npos);
        CHECK(r.error.size() < 120);
    }
    // Echoed names: printable ASCII only, quotes and backslashes made safe.
    seq::ValidationResult quoted = seq::Validate(R"([{"a\"b\\c":1}])");
    CHECK(quoted.error == "invalid step #1: unknown step type a'b/c");
    seq::ValidationResult accented = seq::Validate(R"([{"Mẫu":1}])");
    CHECK(accented.error == "invalid step #1: unknown step type M???u");
    seq::ValidationResult long_key = seq::Validate("[{\"" + Repeat("k", 40) + "\":1}]");
    CHECK(long_key.error == "invalid step #1: unknown step type " + Repeat("k", 24) + "...");
}

TEST_CASE("validated steps round-trip through EstimateStepMs", "[validator][estimate]") {
    seq::ValidationResult r = seq::Validate(kCombatExample);
    REQUIRE(r.ok);
    int sum = 0;
    for (const seq::Step& step : r.sequence.steps) {
        sum += seq::EstimateStepMs(step);
    }
    CHECK(sum == r.sequence.estimated_ms);
}

TEST_CASE("firmware built-in sequences equal interfaces.md open_mask/close_mask",
          "[validator][builtins]") {
    // SequenceRunner::BuiltinSequence lives in sequence_runner.cc (ESP-only), so read its
    // raw string literals back from the source and check them here.
    std::ifstream file(std::string(JARVIS_BOARD_DIR) + "/sequence_runner.cc");
    REQUIRE(file.good());
    std::stringstream buffer;
    buffer << file.rdbuf();
    const std::string source = buffer.str();
    std::vector<std::string> literals;
    for (const char* constant : {"kOpenMaskJson[] =", "kCloseMaskJson[] ="}) {
        INFO("constant " << constant);
        const size_t decl = source.find(constant);
        REQUIRE(decl != std::string::npos);
        const size_t open = source.find("R\"json(", decl);
        REQUIRE(open != std::string::npos);
        const size_t begin = open + 7;
        const size_t end = source.find(")json\"", begin);
        REQUIRE(end != std::string::npos);
        literals.push_back(source.substr(begin, end - begin));
    }
    const std::string expected[2] = {kOpenMask, kCloseMask};
    const int estimates[2] = {1300, 2642};
    for (int i = 0; i < 2; ++i) {
        INFO(literals[i]);
        seq::ValidationResult r = seq::Validate(literals[i]);
        CHECK(r.ok);
        CHECK(r.sequence.estimated_ms == estimates[i]);
        std::unique_ptr<cJSON, decltype(&cJSON_Delete)> actual(cJSON_Parse(literals[i].c_str()),
                                                               &cJSON_Delete);
        std::unique_ptr<cJSON, decltype(&cJSON_Delete)> wanted(cJSON_Parse(expected[i].c_str()),
                                                               &cJSON_Delete);
        REQUIRE(actual != nullptr);
        REQUIRE(wanted != nullptr);
        CHECK(cJSON_Compare(actual.get(), wanted.get(), 1));
    }
}
