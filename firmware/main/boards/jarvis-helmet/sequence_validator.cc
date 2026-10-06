// Sequence parsing and validation (FW4), docs/interfaces.md §3.
//
// Pure C++ plus cJSON: no ESP-IDF headers, so the same file is compiled by the host unit
// tests in firmware/tests/host. Every error message is "invalid step #<n>: <reason>"
// (n counts from 1) or a top-level reason, and is passed through helmet::SanitizeMessage
// because upstream McpServer::ReplyError pastes it into JSON without escaping.
#include "sequence_validator.h"

#include <cJSON.h>

#include <cmath>
#include <cstddef>
#include <cstring>
#include <memory>
#include <string>
#include <utility>

#include "helmet_common.h"

namespace seq {
namespace {

// A valid sequence nests three levels deep (array > step > fields). cJSON parses
// recursively and the MCP callback runs on the 8 KB main task, so absurd nesting is
// refused before the text reaches cJSON.
constexpr int kMaxNestingDepth = 16;

// User-supplied names echoed in error messages are cut to this many characters.
constexpr size_t kEchoMaxChars = 24;

// Display text limits in bytes: a UTF-8 code point takes at most 4 bytes, so for valid
// UTF-8 these never trigger before the code-point limits (same budget as show_info).
constexpr size_t kTitleMaxBytes = helmet::kInfoTitleMaxChars * 4;
constexpr size_t kTextMaxBytes = helmet::kInfoTextMaxChars * 4;

constexpr const char kEffectList[] = "breathe, blink, flicker, power_up, fade_out";

using CJsonPtr = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;

// Copies a user-supplied identifier for an error message: printable ASCII only (other
// bytes become '?', so no broken UTF-8 reaches the reply) and bounded in length.
std::string Echo(const char* s) {
    if (s == nullptr || *s == '\0') {
        return "''";
    }
    std::string out;
    for (size_t i = 0; s[i] != '\0'; ++i) {
        if (out.size() >= kEchoMaxChars) {
            out += "...";
            break;
        }
        unsigned char c = static_cast<unsigned char>(s[i]);
        out.push_back(c >= 0x20 && c < 0x7F ? static_cast<char>(c) : '?');
    }
    return out;
}

std::string RangeError(const char* field, int lo, int hi) {
    return std::string(field) + " out of range " + std::to_string(lo) + ".." + std::to_string(hi);
}

// Integers are JSON numbers with no fractional part (cJSON cannot tell 80 from 80.0);
// booleans are not numbers. The range is checked on the double so huge values cannot
// overflow the conversion.
std::string ReadInt(const cJSON* item, const char* field, int lo, int hi, int& out) {
    if (!cJSON_IsNumber(item)) {
        return std::string(field) + " must be an integer";
    }
    double v = item->valuedouble;
    if (std::isnan(v) || v != std::floor(v)) {
        return std::string(field) + " must be an integer";
    }
    if (v < lo || v > hi) {
        return RangeError(field, lo, hi);
    }
    out = static_cast<int>(v);
    return {};
}

std::string ReadBool(const cJSON* item, const char* field, bool& out) {
    if (!cJSON_IsBool(item)) {
        return std::string(field) + " must be a boolean";
    }
    out = cJSON_IsTrue(item) != 0;
    return {};
}

std::string ReadString(const cJSON* item, const char* field, std::string& out) {
    if (!cJSON_IsString(item) || item->valuestring == nullptr) {
        return std::string(field) + " must be a string";
    }
    out = item->valuestring;
    return {};
}

// Maps the members of a step's field object onto `names` (exact, case-sensitive match;
// cJSON_GetObjectItem would be case-insensitive). Unknown and duplicate names are errors.
template <size_t N>
std::string CollectFields(const cJSON* object, const char* type, const char* const (&names)[N],
                          const cJSON* (&found)[N]) {
    for (const cJSON* child = object->child; child != nullptr; child = child->next) {
        const char* key = child->string != nullptr ? child->string : "";
        size_t i = 0;
        while (i < N && std::strcmp(key, names[i]) != 0) {
            ++i;
        }
        if (i == N) {
            return "unknown field " + Echo(key) + " in " + type;
        }
        if (found[i] != nullptr) {
            return "duplicate field " + Echo(key) + " in " + type;
        }
        found[i] = child;
    }
    return {};
}

std::string MissingField(const char* field) { return std::string("missing field ") + field; }

std::string ParseMask(const cJSON* fields, Step& step) {
    if (!cJSON_IsObject(fields)) {
        return "mask must be an object";
    }
    static constexpr const char* kNames[] = {"action", "position", "speed"};
    const cJSON* found[3] = {};
    std::string error = CollectFields(fields, "mask", kNames, found);
    if (!error.empty()) {
        return error;
    }
    const cJSON* action = found[0];
    const cJSON* position = found[1];
    const cJSON* speed = found[2];
    if ((action == nullptr) == (position == nullptr)) {
        return "mask needs exactly one of action or position";
    }
    if (action != nullptr) {
        std::string value;
        error = ReadString(action, "action", value);
        if (!error.empty()) {
            return error;
        }
        if (value == "open") {
            step.mask_position = helmet::kPositionMax;
        } else if (value == "close") {
            step.mask_position = helmet::kPositionMin;
        } else {
            return "action must be open or close";
        }
        step.mask_by_action = true;
    } else {
        error = ReadInt(position, "position", helmet::kPositionMin, helmet::kPositionMax,
                        step.mask_position);
        if (!error.empty()) {
            return error;
        }
    }
    if (speed != nullptr) {
        return ReadInt(speed, "speed", helmet::kSpeedMin, helmet::kSpeedMax, step.speed);
    }
    return {};
}

std::string ParseServo(const cJSON* fields, Step& step) {
    if (!cJSON_IsObject(fields)) {
        return "servo must be an object";
    }
    static constexpr const char* kNames[] = {"id", "angle", "duration_ms"};
    const cJSON* found[3] = {};
    std::string error = CollectFields(fields, "servo", kNames, found);
    if (!error.empty()) {
        return error;
    }
    if (found[0] == nullptr) {
        return MissingField("id");
    }
    error = ReadInt(found[0], "id", 0, helmet::kServoCount - 1, step.servo_id);
    if (!error.empty()) {
        return error;
    }
    if (found[1] == nullptr) {
        return MissingField("angle");
    }
    error = ReadInt(found[1], "angle", helmet::kAngleMin, helmet::kAngleMax, step.angle);
    if (!error.empty()) {
        return error;
    }
    step.duration_ms = 0;
    if (found[2] != nullptr) {
        return ReadInt(found[2], "duration_ms", 0, helmet::kServoDurationMaxMs, step.duration_ms);
    }
    return {};
}

std::string ParseEyes(const cJSON* fields, Step& step) {
    if (!cJSON_IsObject(fields)) {
        return "eyes must be an object";
    }
    static constexpr const char* kNames[] = {"on", "brightness", "effect", "duration_ms"};
    const cJSON* found[4] = {};
    std::string error = CollectFields(fields, "eyes", kNames, found);
    if (!error.empty()) {
        return error;
    }
    const bool level_form = found[0] != nullptr || found[1] != nullptr;
    const bool effect_form = found[2] != nullptr || found[3] != nullptr;
    if (level_form && effect_form) {
        return "eyes cannot mix on/brightness with effect/duration_ms";
    }
    if (!level_form && !effect_form) {
        return "eyes needs on or effect";
    }
    if (level_form) {
        if (found[0] == nullptr) {
            return MissingField("on");
        }
        error = ReadBool(found[0], "on", step.eyes_on);
        if (!error.empty()) {
            return error;
        }
        if (found[1] != nullptr) {
            return ReadInt(found[1], "brightness", helmet::kBrightnessMin, helmet::kBrightnessMax,
                           step.brightness);
        }
        return {};
    }
    if (found[2] == nullptr) {
        return MissingField("effect");
    }
    error = ReadString(found[2], "effect", step.effect);
    if (!error.empty()) {
        return error;
    }
    if (!helmet::IsEyeEffect(step.effect)) {
        return "unknown effect " + Echo(step.effect.c_str()) + " (use " + kEffectList + ")";
    }
    step.eyes_effect = true;
    step.duration_ms = helmet::kDefaultEffectDurationMs;
    if (found[3] != nullptr) {
        return ReadInt(found[3], "duration_ms", helmet::kEffectDurationMinMs,
                       helmet::kEffectDurationMaxMs, step.duration_ms);
    }
    return {};
}

std::string ParseOutput(const cJSON* fields, Step& step) {
    if (!cJSON_IsObject(fields)) {
        return "output must be an object";
    }
    static constexpr const char* kNames[] = {"on"};
    const cJSON* found[1] = {};
    std::string error = CollectFields(fields, "output", kNames, found);
    if (!error.empty()) {
        return error;
    }
    if (found[0] == nullptr) {
        return MissingField("on");
    }
    return ReadBool(found[0], "on", step.output_on);
}

std::string ParseDisplay(const cJSON* fields, Step& step) {
    if (!cJSON_IsObject(fields)) {
        return "display must be an object";
    }
    static constexpr const char* kNames[] = {"title", "text", "seconds"};
    const cJSON* found[3] = {};
    std::string error = CollectFields(fields, "display", kNames, found);
    if (!error.empty()) {
        return error;
    }
    if (found[0] == nullptr) {
        return MissingField("title");
    }
    error = ReadString(found[0], "title", step.title);
    if (!error.empty()) {
        return error;
    }
    // An empty title is allowed (text-only card), as on the server (interfaces.md §3 only
    // requires the field and caps it at 24 chars).
    if (helmet::Utf8Length(step.title) > static_cast<size_t>(helmet::kInfoTitleMaxChars) ||
        step.title.size() > kTitleMaxBytes) {
        return "title too long (max " + std::to_string(helmet::kInfoTitleMaxChars) + " chars)";
    }
    if (found[1] == nullptr) {
        return MissingField("text");
    }
    error = ReadString(found[1], "text", step.text);
    if (!error.empty()) {
        return error;
    }
    if (helmet::Utf8Length(step.text) > static_cast<size_t>(helmet::kInfoTextMaxChars) ||
        step.text.size() > kTextMaxBytes) {
        return "text too long (max " + std::to_string(helmet::kInfoTextMaxChars) + " chars)";
    }
    step.seconds = helmet::kDefaultInfoSeconds;
    if (found[2] != nullptr) {
        return ReadInt(found[2], "seconds", helmet::kInfoSecondsMin, helmet::kInfoSecondsMax,
                       step.seconds);
    }
    return {};
}

std::string ParseSound(const cJSON* fields, Step& step) {
    if (!cJSON_IsObject(fields)) {
        return "sound must be an object";
    }
    static constexpr const char* kNames[] = {"name"};
    const cJSON* found[1] = {};
    std::string error = CollectFields(fields, "sound", kNames, found);
    if (!error.empty()) {
        return error;
    }
    if (found[0] == nullptr) {
        return MissingField("name");
    }
    error = ReadString(found[0], "name", step.sound);
    if (!error.empty()) {
        return error;
    }
    if (!helmet::IsValidSoundName(step.sound)) {
        return "sound name must match [a-z0-9_]{1," + std::to_string(helmet::kSoundNameMaxLen) +
               "}";
    }
    return {};
}

// Returns the reason why `item` is not a valid step, or an empty string.
std::string ParseStep(const cJSON* item, Step& step) {
    if (!cJSON_IsObject(item)) {
        return "step must be an object";
    }
    int keys = 0;
    for (const cJSON* child = item->child; child != nullptr; child = child->next) {
        ++keys;
    }
    if (keys != 1) {
        return "step must have exactly one key (got " + std::to_string(keys) + ")";
    }
    const cJSON* entry = item->child;
    const char* key = entry->string != nullptr ? entry->string : "";
    if (std::strcmp(key, "mask") == 0) {
        step.type = StepType::kMask;
        return ParseMask(entry, step);
    }
    if (std::strcmp(key, "servo") == 0) {
        step.type = StepType::kServo;
        return ParseServo(entry, step);
    }
    if (std::strcmp(key, "eyes") == 0) {
        step.type = StepType::kEyes;
        return ParseEyes(entry, step);
    }
    if (std::strcmp(key, "output") == 0) {
        step.type = StepType::kOutput;
        return ParseOutput(entry, step);
    }
    if (std::strcmp(key, "delay") == 0) {
        step.type = StepType::kDelay;
        return ReadInt(entry, "delay", 0, helmet::kDelayMaxMs, step.delay_ms);
    }
    if (std::strcmp(key, "display") == 0) {
        step.type = StepType::kDisplay;
        return ParseDisplay(entry, step);
    }
    if (std::strcmp(key, "sound") == 0) {
        step.type = StepType::kSound;
        return ParseSound(entry, step);
    }
    return "unknown step type " + Echo(key);
}

}  // namespace

int EstimateStepMs(const Step& step) {
    switch (step.type) {
        case StepType::kMask:
            return helmet::MaskFullTravelMs(step.speed > 0 ? step.speed : helmet::kDefaultSpeed);
        case StepType::kServo:
            return step.duration_ms;
        case StepType::kEyes:
            return step.eyes_effect && step.effect != "breathe" ? step.duration_ms : 0;
        case StepType::kDelay:
            return step.delay_ms;
        case StepType::kOutput:
        case StepType::kDisplay:
        case StepType::kSound:
            break;
    }
    return 0;
}

ValidationResult Validate(const std::string& json) {
    ValidationResult result;
    auto fail = [&result](const std::string& reason) {
        result.ok = false;
        result.error = helmet::SanitizeMessage(reason);
        return std::move(result);
    };

    if (!helmet::JsonNestingWithin(json, kMaxNestingDepth)) {
        return fail("steps must be a JSON array (nesting too deep)");
    }
    // The length includes the terminating NUL so that require_null_terminated rejects any
    // trailing garbage after the array ("[] x").
    CJsonPtr root(cJSON_ParseWithLengthOpts(json.c_str(), json.size() + 1, nullptr, 1),
                  &cJSON_Delete);
    if (root == nullptr) {
        return fail("steps must be a JSON array (invalid JSON)");
    }
    if (!cJSON_IsArray(root.get())) {
        return fail("steps must be a JSON array");
    }
    const int count = cJSON_GetArraySize(root.get());
    if (count > helmet::kMaxSteps) {
        return fail("too many steps (max " + std::to_string(helmet::kMaxSteps) + ")");
    }

    Sequence sequence;
    sequence.steps.reserve(static_cast<size_t>(count));
    int number = 0;
    for (const cJSON* item = root->child; item != nullptr; item = item->next) {
        ++number;
        Step step;
        std::string reason = ParseStep(item, step);
        if (!reason.empty()) {
            return fail("invalid step #" + std::to_string(number) + ": " + reason);
        }
        sequence.estimated_ms += EstimateStepMs(step);
        sequence.steps.push_back(std::move(step));
    }

    if (sequence.estimated_ms > helmet::kMaxSequenceMs) {
        const int estimated = sequence.estimated_ms;
        // Keep the parsed steps and the estimate so callers and tests can inspect them.
        result.sequence = std::move(sequence);
        return fail("sequence too long: " + std::to_string(estimated) + " ms (max " +
                    std::to_string(helmet::kMaxSequenceMs) + ")");
    }

    result.ok = true;
    result.sequence = std::move(sequence);
    return result;
}

}  // namespace seq
