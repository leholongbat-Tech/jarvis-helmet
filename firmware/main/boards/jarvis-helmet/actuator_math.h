// Pure actuator math for the Jarvis Helmet (FW2 mask servos, FW3 eye lights).
//
// No ESP-IDF includes: shared by mask_controller.cc, output_controller.cc and the host
// unit tests (firmware/tests/host/test_actuator_math.cpp), and kept C++17 compatible.
// Units: angles in degrees, mask positions in percent (0 = closed, 100 = open),
// brightness in perceived percent, times in the unit of the caller unless noted.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>

namespace actuator {

constexpr float kPi = 3.14159265358979f;

// ---- Servo pulse and LEDC duty ---------------------------------------------------------

// Pulse width for an angle (clamped to 0..180): min_us + angle / 180 * (max_us - min_us).
inline float AngleToPulseUs(float angle_deg, int min_pulse_us, int max_pulse_us) {
    const float angle = std::clamp(angle_deg, 0.0f, 180.0f);
    return static_cast<float>(min_pulse_us) +
           angle / 180.0f * static_cast<float>(max_pulse_us - min_pulse_us);
}

// Full-scale duty of an LEDC timer: 2^bits (that value keeps the output constantly high).
constexpr uint32_t MaxDuty(int resolution_bits) {
    return static_cast<uint32_t>(1u) << resolution_bits;
}

// LEDC duty for a pulse width: pulse / period * 2^bits, rounded, clamped to [0, 2^bits].
inline uint32_t PulseUsToDuty(float pulse_us, int resolution_bits, int freq_hz) {
    if (pulse_us <= 0.0f || freq_hz <= 0) {
        return 0;
    }
    const float period_us = 1000000.0f / static_cast<float>(freq_hz);
    const float max_duty = static_cast<float>(MaxDuty(resolution_bits));
    const float duty = std::min(pulse_us / period_us * max_duty, max_duty);
    return static_cast<uint32_t>(duty + 0.5f);
}

// ---- Calibration and positions --------------------------------------------------------

// A calibration pair is usable when both angles are within 0..180 and they differ.
inline bool IsValidCalibration(int closed_angle, int open_angle) {
    return closed_angle >= 0 && closed_angle <= 180 && open_angle >= 0 && open_angle <= 180 &&
           closed_angle != open_angle;
}

// Clamps an angle into the calibrated range [min(closed, open), max(closed, open)].
inline float ClampToCalibration(float angle_deg, int closed_angle, int open_angle) {
    const float lo = static_cast<float>(std::min(closed_angle, open_angle));
    const float hi = static_cast<float>(std::max(closed_angle, open_angle));
    return std::clamp(angle_deg, lo, hi);
}

// Mask position (clamped to 0..100) to a servo angle, linear between the calibrated
// angles. A mirrored servo simply has closed > open, so no invert flag is needed.
inline float PositionToAngle(float position, int closed_angle, int open_angle) {
    const float p = std::clamp(position, 0.0f, 100.0f);
    return static_cast<float>(closed_angle) +
           static_cast<float>(open_angle - closed_angle) * p / 100.0f;
}

// Inverse of PositionToAngle, clamped to 0..100 (0 for a degenerate pair).
inline float AngleToPosition(float angle_deg, int closed_angle, int open_angle) {
    if (closed_angle == open_angle) {
        return 0.0f;
    }
    const float p = (angle_deg - static_cast<float>(closed_angle)) * 100.0f /
                    static_cast<float>(open_angle - closed_angle);
    return std::clamp(p, 0.0f, 100.0f);
}

// ---- Motion profile -------------------------------------------------------------------

// Cosine ease-in-out on [0, 1]: 0 -> 0, 0.5 -> 0.5, 1 -> 1, zero velocity at both ends.
inline float EaseInOutCosine(float t) {
    const float x = std::clamp(t, 0.0f, 1.0f);
    return 0.5f - 0.5f * std::cos(kPi * x);
}

// Eased value of a move from `from` to `to` that started `elapsed` ago and lasts
// `duration` (same unit). Before the start it holds `from`; duration <= 0 jumps to `to`.
inline float EasedValue(float from, float to, float elapsed, float duration) {
    if (duration <= 0.0f || elapsed >= duration) {
        return to;
    }
    if (elapsed <= 0.0f) {
        return from;
    }
    return from + (to - from) * EaseInOutCosine(elapsed / duration);
}

// Staggered start of the second servo: it starts `delay_ms` later (at most half the move)
// and runs for total_ms - delay so both servos finish together. Returns the delay to use.
inline int StaggerDelayMs(int total_ms, int delay_ms) {
    if (total_ms <= 0 || delay_ms <= 0) {
        return 0;
    }
    return std::min(delay_ms, total_ms / 2);
}

// Duration of a move proportional to its travel, never shorter than min_ms.
inline int TravelMs(float from_deg, float to_deg, float ms_per_degree, int min_ms) {
    const int ms = static_cast<int>(std::fabs(to_deg - from_deg) * ms_per_degree + 0.5f);
    return std::max(ms, min_ms);
}

// Duration of a mask move from the servos' real angles `from_deg[s]` to `to_deg[s]`, timed
// on the servo with the longest way: `full_travel_ms` per full calibrated span
// |open - closed|, the distance in whole percent (as helmet::MaskMoveMs rounds it), at least
// min_ms and at most max_ms. Positions are NOT clamped to 0..100, so a servo left outside
// its calibrated range (raw calibration) eases back at the requested speed instead of
// snapping. Returns 0 (settle in place) only when the distance rounds to 0 % and every
// servo is within 0.5 degrees of its target. For servos inside their range this equals
// helmet::MaskMoveMs (except that a sub-percent step of 0.5 degrees or more on a very wide
// calibration gets the minimum move instead of a jump).
inline int MaskMoveDurationMs(const float* from_deg, const float* to_deg, const int* closed,
                              const int* open, int servos, int full_travel_ms, int min_ms,
                              int max_ms) {
    long distance = 0;
    float max_delta = 0.0f;
    for (int s = 0; s < servos; ++s) {
        const int span = open[s] - closed[s];
        if (span == 0) {
            continue;
        }
        max_delta = std::max(max_delta, std::fabs(to_deg[s] - from_deg[s]));
        const float from_pct =
            (from_deg[s] - static_cast<float>(closed[s])) * 100.0f / static_cast<float>(span);
        const float to_pct =
            (to_deg[s] - static_cast<float>(closed[s])) * 100.0f / static_cast<float>(span);
        distance = std::max(distance, std::labs(std::lround(to_pct) - std::lround(from_pct)));
    }
    if (distance == 0 && max_delta < 0.5f) {
        return 0;
    }
    const long ms = static_cast<long>(full_travel_ms) * std::max(distance, 1L) / 100;
    return static_cast<int>(std::min<long>(std::max<long>(ms, min_ms), std::max(max_ms, min_ms)));
}

// Angle a servo gets at boot: its own resting angle stored at the last stop (valid 0..180),
// clamped into the calibrated range, otherwise the angle of the stored mask position.
// Restoring each servo's own angle keeps both still at power-up even after a move of only
// one servo (sequence "servo" step, raw calibration), where the two disagree.
inline float BootAngle(int stored_angle, float mask_position, int closed_angle, int open_angle) {
    if (stored_angle >= 0 && stored_angle <= 180) {
        return ClampToCalibration(static_cast<float>(stored_angle), closed_angle, open_angle);
    }
    return ClampToCalibration(PositionToAngle(mask_position, closed_angle, open_angle),
                              closed_angle, open_angle);
}

// ---- Light output ---------------------------------------------------------------------

// Gamma-corrected duty for a perceived brightness 0..100: max_duty * (b / 100)^gamma,
// rounded. Any brightness above 0 yields at least duty 1 so a dim setting never reads as off.
inline uint32_t GammaDuty(float brightness_pct, uint32_t max_duty, float gamma) {
    if (brightness_pct <= 0.0f || max_duty == 0) {
        return 0;
    }
    if (brightness_pct >= 100.0f) {
        return max_duty;
    }
    const float duty = static_cast<float>(max_duty) * std::pow(brightness_pct / 100.0f, gamma);
    const uint32_t rounded = static_cast<uint32_t>(duty + 0.5f);
    return std::clamp<uint32_t>(rounded, 1u, max_duty);
}

// Inverse of GammaDuty: perceived brightness 0..100 of a duty (used to resume from the
// live duty when a fade is interrupted).
inline float DutyToBrightness(uint32_t duty, uint32_t max_duty, float gamma) {
    if (duty == 0 || max_duty == 0 || gamma <= 0.0f) {
        return 0.0f;
    }
    if (duty >= max_duty) {
        return 100.0f;
    }
    const float ratio = static_cast<float>(duty) / static_cast<float>(max_duty);
    return 100.0f * std::pow(ratio, 1.0f / gamma);
}

// Linear ramp of perceived brightness (the gamma mapping makes it look even).
inline float RampLevel(float from, float to, float elapsed, float duration) {
    if (duration <= 0.0f || elapsed >= duration) {
        return to;
    }
    if (elapsed <= 0.0f) {
        return from;
    }
    return from + (to - from) * elapsed / duration;
}

// Breathing curve: lo at t = 0, hi at half a period, lo again after a full period.
inline float BreatheLevel(float t, float period, float lo, float hi) {
    if (period <= 0.0f) {
        return hi;
    }
    const float phase = std::fmod(std::max(t, 0.0f), period) / period;
    return lo + (hi - lo) * (0.5f - 0.5f * std::cos(2.0f * kPi * phase));
}

// Eye brightness while the assistant speaks: follows the speaker level (0..1, scaled by
// gain and clipped) between floor_fraction * base and base.
inline float SpeechBrightness(float base, float speech_level, float gain, float floor_fraction) {
    const float level = std::clamp(speech_level * gain, 0.0f, 1.0f);
    const float min_fraction = std::clamp(floor_fraction, 0.0f, 1.0f);
    return base * (min_fraction + (1.0f - min_fraction) * level);
}

}  // namespace actuator
