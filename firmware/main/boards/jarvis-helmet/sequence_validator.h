// Sequence parsing and validation (FW4), docs/interfaces.md §3.
//
// Pure C++ plus cJSON so it builds both in the firmware and in the host unit tests
// (firmware/tests/host). The whole sequence is checked before anything moves.
#pragma once

#include <string>
#include <vector>

namespace seq {

enum class StepType { kMask, kServo, kEyes, kOutput, kDelay, kDisplay, kSound };

struct Step {
    StepType type = StepType::kDelay;

    // mask: {"action":"open"|"close"} or {"position":0..100}, optional "speed" 10..100
    bool mask_by_action = false;
    int mask_position = 0;  // action open -> 100, close -> 0
    int speed = -1;         // -1 = controller default

    // servo: {"id":0..1,"angle":0..180,"duration_ms":0..5000}
    int servo_id = 0;
    int angle = 0;

    // eyes: {"on":bool,"brightness":0..100} or {"effect":name,"duration_ms":100..10000}
    bool eyes_effect = false;
    bool eyes_on = false;
    int brightness = -1;  // -1 = controller default
    std::string effect;

    // servo duration or eyes effect duration
    int duration_ms = 0;

    // output: {"on":bool}
    bool output_on = false;

    // delay: integer 0..10000
    int delay_ms = 0;

    // display: {"title":<=24 chars,"text":<=200 chars,"seconds":1..30}
    std::string title;
    std::string text;
    int seconds = 0;

    // sound: {"name":[a-z0-9_]{1,32}}
    std::string sound;
};

struct Sequence {
    std::vector<Step> steps;
    int estimated_ms = 0;  // sum of EstimateStepMs over all steps
};

struct ValidationResult {
    bool ok = false;
    // On failure: "invalid step #<n>: <reason>" (n counts from 1) or a top-level reason.
    // Always JSON-safe (passed through helmet::SanitizeMessage).
    std::string error;
    Sequence sequence;
};

// Parses a JSON array of single-key step objects and checks every rule of
// interfaces.md §3: known step types and fields only, value ranges, at most 64 steps and
// an estimated total of at most 60 s.
ValidationResult Validate(const std::string& json);

// Worst-case blocking time of one step: mask = full travel at its speed (default 70),
// servo = duration_ms, eyes effect = duration_ms (breathe = 0), delay = its value,
// everything else 0.
int EstimateStepMs(const Step& step);

}  // namespace seq
