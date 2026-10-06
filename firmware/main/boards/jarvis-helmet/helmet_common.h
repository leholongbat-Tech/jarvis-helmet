// Shared limits and helpers for the Jarvis Helmet firmware.
//
// Used by the controllers, the sequence validator and the host unit tests, so this
// header must stay pure C++ (no ESP-IDF includes). Ranges follow docs/interfaces.md.
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdlib>
#include <string>

namespace helmet {

// Motion and actuator ranges (interfaces.md §2, §3).
constexpr int kSpeedMin = 10;
constexpr int kSpeedMax = 100;
constexpr int kDefaultSpeed = 70;
constexpr int kPositionMin = 0;    // closed
constexpr int kPositionMax = 100;  // open
constexpr int kServoCount = 2;     // 0 = left (S0), 1 = right (S1, mirrored)
constexpr int kAngleMin = 0;
constexpr int kAngleMax = 180;
constexpr int kServoDurationMaxMs = 5000;
constexpr int kBrightnessMin = 0;
constexpr int kBrightnessMax = 100;
constexpr int kDefaultBrightness = 100;
constexpr int kEffectDurationMinMs = 100;
constexpr int kEffectDurationMaxMs = 10000;
constexpr int kDefaultEffectDurationMs = 1000;
constexpr int kBreathePeriodMs = 2500;
constexpr int kDelayMaxMs = 10000;

// HUD info card (self.display.show_info). Limits count UTF-8 code points.
constexpr int kInfoTitleMaxChars = 24;
constexpr int kInfoTextMaxChars = 200;
constexpr int kInfoSecondsMin = 1;
constexpr int kInfoSecondsMax = 30;
constexpr int kDefaultInfoSeconds = 5;

// Sequences (interfaces.md §3, §4).
constexpr int kMaxSteps = 64;
constexpr int kMaxSequenceMs = 60000;
constexpr size_t kSequenceNameMaxLen = 11;  // NVS key "seq_<name>" must fit in 15 chars
constexpr size_t kSoundNameMaxLen = 32;
constexpr size_t kMaxSequenceJsonBytes = 4000;  // NVS blob budget per saved sequence

// Mask motion timing (FW2): a full 0 -> 100 travel takes kMaskBaseMs * 100 / speed,
// shorter moves scale with distance but never take less than kMaskMinMoveMs.
constexpr int kMaskBaseMs = 800;
constexpr int kMaskMinMoveMs = 150;

inline int ClampInt(int value, int lo, int hi) {
    return std::min(std::max(value, lo), hi);
}

inline int MaskFullTravelMs(int speed) {
    return kMaskBaseMs * 100 / ClampInt(speed, kSpeedMin, kSpeedMax);
}

inline int MaskMoveMs(int from_percent, int to_percent, int speed) {
    int distance = std::abs(ClampInt(to_percent, kPositionMin, kPositionMax) -
                            ClampInt(from_percent, kPositionMin, kPositionMax));
    if (distance == 0) {
        return 0;
    }
    return std::max(MaskFullTravelMs(speed) * distance / 100, kMaskMinMoveMs);
}

// Eye effects (FW3).
inline constexpr const char* kEyeEffects[] = {"breathe", "blink", "flicker", "power_up",
                                              "fade_out"};

inline bool IsEyeEffect(const std::string& name) {
    for (const char* effect : kEyeEffects) {
        if (name == effect) {
            return true;
        }
    }
    return false;
}

// Effect duration when duration_ms is 0 or absent (D26): breathe = cycle period, flicker = the
// short "failing lamp" burst, the others ramp/blink over 1 s. Same table in server/tasks/taskslib.py.
constexpr int kDefaultFlickerMs = 600;

inline int EyeEffectDefaultMs(const std::string& name) {
    if (name == "breathe") {
        return kBreathePeriodMs;
    }
    if (name == "flicker") {
        return kDefaultFlickerMs;
    }
    return kDefaultEffectDurationMs;
}

// duration_ms of an eye effect: 0 (= effect default) or kEffectDurationMinMs..kEffectDurationMaxMs.
inline bool IsValidEffectDuration(int ms) {
    return ms == 0 || (ms >= kEffectDurationMinMs && ms <= kEffectDurationMaxMs);
}

inline int ResolveEffectMs(const std::string& name, int ms) {
    return ms > 0 ? ms : EyeEffectDefaultMs(name);
}

inline bool IsLowerSnake(const std::string& s, size_t max_len) {
    if (s.empty() || s.size() > max_len) {
        return false;
    }
    for (char c : s) {
        bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
        if (!ok) {
            return false;
        }
    }
    return true;
}

// [a-z0-9_]{1,11}
inline bool IsValidSequenceName(const std::string& name) {
    return IsLowerSnake(name, kSequenceNameMaxLen);
}

// [a-z0-9_]{1,32}
inline bool IsValidSoundName(const std::string& name) {
    return IsLowerSnake(name, kSoundNameMaxLen);
}

// True when no '[' / '{' outside JSON strings nests deeper than `max_depth`. cJSON parses
// recursively (about 64 bytes of stack per level on the ESP32-S3), so untrusted text is
// checked with this before it reaches cJSON on a small task stack.
inline bool JsonNestingWithin(const char* data, size_t size, int max_depth) {
    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    for (size_t i = 0; i < size; ++i) {
        const char c = data[i];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (c == '\\') {
                escaped = true;
            } else if (c == '"') {
                in_string = false;
            }
            continue;
        }
        if (c == '"') {
            in_string = true;
        } else if (c == '[' || c == '{') {
            if (++depth > max_depth) {
                return false;
            }
        } else if ((c == ']' || c == '}') && depth > 0) {
            --depth;
        }
    }
    return true;
}

inline bool JsonNestingWithin(const std::string& json, int max_depth) {
    return JsonNestingWithin(json.data(), json.size(), max_depth);
}

// Number of UTF-8 code points; stray continuation bytes are not counted.
inline size_t Utf8Length(const std::string& s) {
    size_t count = 0;
    for (unsigned char c : s) {
        if ((c & 0xC0) != 0x80) {
            ++count;
        }
    }
    return count;
}

// Upstream McpServer::ReplyError pastes the message into JSON without escaping it, so
// every error text returned through std::unexpected must go through this first.
inline std::string SanitizeMessage(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (unsigned char c : s) {
        if (c == '"') {
            out.push_back('\'');
        } else if (c == '\\') {
            out.push_back('/');
        } else if (c < 0x20 || c == 0x7F) {
            out.push_back(' ');
        } else {
            out.push_back(static_cast<char>(c));
        }
    }
    return out;
}

}  // namespace helmet
